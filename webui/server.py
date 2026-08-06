"""WebUI 控制台:登录认证 + 管理 API + 实时聊天 WebSocket。

密码用 PBKDF2 哈希存储,登录颁发会话 token;未配置密码时启动随机生成。
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import secrets
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from aiohttp import WSMsgType, web
from loguru import logger

from ..adapter.event import AgentEvent
from ..adapter.message import MessageChain, MessageSegment
from ..utils.config import Config

STATIC_DIR = Path(__file__).parent / "static"
ROOT_DIR = Path(__file__).resolve().parent.parent.parent


def _tail_jsonl(path: Path, limit: int = 100, max_bytes: int = 1_000_000) -> list[dict]:
    """从文件尾部读取最多 limit 条 JSON 行(避免整体加载大文件)。"""
    if not path.is_file():
        return []
    try:
        size = path.stat().st_size
        with open(path, "rb") as f:
            f.seek(max(0, size - max_bytes))
            f.readline()  # 跳过可能被截断的首行
            raw = f.read().decode("utf-8", "ignore").splitlines()
    except OSError:
        return []
    entries: list[dict] = []
    for line in raw[-limit:]:
        try:
            entries.append(json.loads(line))
        except (json.JSONDecodeError, TypeError):
            continue
    return entries


def _pbkdf2_hash(password: str, salt: bytes | None = None, iterations: int = 100_000) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return f"pbkdf2${iterations}${salt.hex()}${digest.hex()}"


def _verify_pbkdf2(password: str, stored: str) -> bool:
    try:
        scheme, iterations, salt_hex, digest_hex = stored.split("$")
        if scheme != "pbkdf2":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), bytes.fromhex(salt_hex), int(iterations)
        )
        return hmac.compare_digest(digest.hex(), digest_hex)
    except ValueError:
        return False


class WebUIServer:
    def __init__(self, config: Config, host: str = "127.0.0.1", port: int = 8080, deps: dict | None = None):
        self._config = config
        self._host = host
        self._port = port
        self._deps: dict[str, Any] = dict(deps or {})
        self._app = web.Application()
        self._runner: web.AppRunner | None = None
        self._start_time = time.time()
        self._tokens: dict[str, float] = {}
        self._password = ""
        self._password_hash = ""
        self._setup_auth()
        self._setup_routes()

    def _setup_auth(self) -> None:
        ph = self._config.get("webui.password_hash", "")
        pw = self._config.get("webui.password", "")
        if ph:
            self._password_hash = str(ph)
        elif pw:
            self._password = str(pw)
            self._password_hash = _pbkdf2_hash(self._password)
        else:
            self._password = secrets.token_urlsafe(6)
            self._password_hash = _pbkdf2_hash(self._password)
            logger.warning(f"WebUI 未配置密码,已随机生成: {self._password}")

    def _setup_routes(self) -> None:
        self._app.router.add_get("/", self._index)
        self._app.router.add_post("/api/login", self._login)
        self._app.router.add_get("/api/status", self._status)
        self._app.router.add_get("/api/tools", self._tools)
        self._app.router.add_get("/api/tasks", self._tasks)
        self._app.router.add_get("/api/mcp", self._mcp_status)
        self._app.router.add_get("/api/skills", self._skills)
        self._app.router.add_get("/api/audit", self._audit)
        self._app.router.add_get("/api/subagents", self._subagents)
        self._app.router.add_post("/api/broadcast", self._broadcast)
        self._app.router.add_get("/ws/chat", self._chat_ws)

    # ---------- 生命周期 ----------

    async def start(self) -> None:
        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self._host, self._port)
        await site.start()
        logger.info(f"WebUI 已启动: http://{self._host}:{self._port}")

    async def stop(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None

    # ---------- 认证 ----------

    def _check_auth(self, request: web.Request) -> bool:
        token = request.headers.get("Authorization", "").removeprefix("Bearer ")
        expire = self._tokens.get(token, 0)
        if expire < time.time():
            return False
        return True

    async def _login(self, request: web.Request) -> web.Response:
        try:
            data = await request.json()
        except json.JSONDecodeError:
            return web.json_response({"ok": False, "error": "无效请求"}, status=400)
        password = data.get("password", "")
        if self._password:
            ok = secrets.compare_digest(password, self._password)
        else:
            ok = _verify_pbkdf2(password, self._password_hash)
        if not ok:
            return web.json_response({"ok": False, "error": "密码错误"}, status=401)
        token = secrets.token_hex(32)
        self._tokens[token] = time.time() + 24 * 3600
        return web.json_response({"ok": True, "token": token})

    # ---------- 页面 ----------

    async def _index(self, request: web.Request) -> web.Response:
        index = STATIC_DIR / "index.html"
        content = index.read_text(encoding="utf-8") if index.exists() else "WebUI"
        return web.Response(text=content, content_type="text/html", charset="utf-8")

    # ---------- API ----------

    async def _status(self, request: web.Request) -> web.Response:
        if not self._check_auth(request):
            return web.json_response({"ok": False, "error": "未授权"}, status=401)
        engine = self._deps.get("engine")
        tools = self._deps.get("tools")
        stats = engine.stats() if engine and hasattr(engine, "stats") else {}
        return web.json_response(
            {
                "ok": True,
                "uptime": int(time.time() - self._start_time),
                "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "model": self._config.get("llm.provider.model", ""),
                "provider": self._config.get("llm.provider.type", ""),
                "tools_count": len(tools.names()) if tools else 0,
                "messages_processed": stats.get("messages_processed", 0),
                "connected": bool(self._deps.get("adapter_connected")),
            }
        )

    async def _tools(self, request: web.Request) -> web.Response:
        if not self._check_auth(request):
            return web.json_response({"ok": False, "error": "未授权"}, status=401)
        tools = self._deps.get("tools")
        if not tools:
            return web.json_response({"ok": True, "tools": []})
        return web.json_response(
            {
                "ok": True,
                "tools": [
                    {"name": t.name, "description": t.description}
                    for t in tools.all()
                ],
            }
        )

    async def _tasks(self, request: web.Request) -> web.Response:
        if not self._check_auth(request):
            return web.json_response({"ok": False, "error": "未授权"}, status=401)
        cron = self._deps.get("cron")
        tasks = cron.list_tasks() if cron else []
        return web.json_response({"ok": True, "tasks": tasks})

    async def _mcp_status(self, request: web.Request) -> web.Response:
        if not self._check_auth(request):
            return web.json_response({"ok": False, "error": "未授权"}, status=401)
        engine = self._deps.get("engine")
        servers = (
            engine.mcp_manager.list_status() if engine and engine.mcp_manager else []
        )
        return web.json_response({"ok": True, "servers": servers})

    async def _skills(self, request: web.Request) -> web.Response:
        if not self._check_auth(request):
            return web.json_response({"ok": False, "error": "未授权"}, status=401)
        engine = self._deps.get("engine")
        skills = engine.skills.all() if engine and engine.skills else []
        return web.json_response(
            {
                "ok": True,
                "skills": [
                    {
                        "name": s.name,
                        "description": s.description,
                        "content": s.content[:500],
                    }
                    for s in skills
                ],
            }
        )

    async def _audit(self, request: web.Request) -> web.Response:
        if not self._check_auth(request):
            return web.json_response({"ok": False, "error": "未授权"}, status=401)
        audit_path = ROOT_DIR / "data" / "audit.jsonl"
        return web.json_response(
            {"ok": True, "audit": _tail_jsonl(audit_path, limit=200)}
        )

    async def _subagents(self, request: web.Request) -> web.Response:
        if not self._check_auth(request):
            return web.json_response({"ok": False, "error": "未授权"}, status=401)
        engine = self._deps.get("engine")
        mgr = engine.subagent_manager if engine else None
        running = mgr.running() if mgr else []
        results = mgr._results if mgr else {}
        history = mgr.recent(20) if mgr else []
        return web.json_response(
            {
                "ok": True,
                "running": running,
                "results": results,
                "history": history,
            }
        )

    async def _broadcast(self, request: web.Request) -> web.Response:
        """管理员从 WebUI 向指定 QQ 群发送消息。"""
        if not self._check_auth(request):
            return web.json_response({"ok": False, "error": "未授权"}, status=401)
        try:
            data = await request.json()
        except json.JSONDecodeError:
            return web.json_response({"ok": False, "error": "无效请求"}, status=400)
        group_id = str(data.get("group_id", "") or "")
        text = str(data.get("text", "") or "")
        if not group_id or not text:
            return web.json_response(
                {"ok": False, "error": "需要 group_id 和 text"}, status=400
            )
        cron = self._deps.get("cron")
        adapter = cron._adapter if cron is not None else None
        if adapter is None or not hasattr(adapter, "send_group_msg"):
            return web.json_response(
                {"ok": False, "error": "QQ 适配器未连接"}, status=500
            )
        try:
            await adapter.send_group_msg(group_id, text)
        except Exception as exc:  # noqa: BLE001
            logger.exception("WebUI 广播失败")
            return web.json_response({"ok": False, "error": str(exc)}, status=500)
        return web.json_response({"ok": True, "group_id": group_id})

    # ---------- 聊天 ----------

    async def _chat_ws(self, request: web.Request) -> web.WebSocketResponse:
        if not self._check_auth(request):
            return web.Response(status=401, text="Unauthorized")
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        engine = self._deps.get("engine")
        async for msg in ws:
            if msg.type != WSMsgType.TEXT:
                continue
            try:
                data = json.loads(msg.data)
            except json.JSONDecodeError:
                continue
            text = data.get("text", "")
            if not text:
                continue
            user_id = data.get("user_id", "webui")
            event = AgentEvent(
                message_type="private",
                user_id=user_id,
                sender_name=user_id,
                session_id=f"webui:{user_id}",
                message=MessageChain([MessageSegment.text(text)]),
                is_tome=True,
            )

            async def _cb(_event: AgentEvent, _text: str, at: bool = False) -> None:
                if _text and not ws.closed:
                    await ws.send_str(json.dumps({"role": "assistant", "text": _text}))

            event._send_callback = _cb
            try:
                reply = await engine.process(event)
            except Exception:  # noqa: BLE001
                logger.exception("WebUI 聊天处理异常")
                reply = "处理出错,请查看服务端日志。"
            if reply and not ws.closed:
                await ws.send_str(json.dumps({"role": "assistant", "text": reply}))
        return ws
