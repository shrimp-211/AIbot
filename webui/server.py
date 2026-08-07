"""WebUI 控制台:登录认证 + 管理 API + 实时聊天 WebSocket。

密码用 PBKDF2 哈希存储,登录颁发会话 token;未配置密码时启动随机生成。
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import re
import secrets
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from aiohttp import WSMsgType, web
from loguru import logger
import yaml

from ..adapter.event import AgentEvent
from ..adapter.message import MessageChain, MessageSegment, escape_cq
from ..utils.config import Config, save_config, validate_config

# 配置视图中的敏感项掩码:不向浏览器泄露 api_key/app_secret/token 等明文
_SECRET_MASK = "***(已配置)***"


def _safe_int(value: Any, default: int, lo: int | None = None, hi: int | None = None) -> int:
    """安全解析整数入参,非法值回退默认,并夹在 [lo, hi] 区间。"""
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    if lo is not None:
        n = max(lo, n)
    if hi is not None:
        n = min(hi, n)
    return n


def _is_secret_key(key: str) -> bool:
    k = key.lower()
    return (
        k in ("api_key", "app_secret", "sign_secret", "password", "token", "access_token")
        or "secret" in k
        or k.endswith("_key")
        or k.endswith("_token")
        or k.endswith("_hash")
    )


def _mask_secrets(data: Any) -> Any:
    """递归掩码非空敏感值,供配置视图展示。"""
    if isinstance(data, dict):
        return {
            k: (_SECRET_MASK if _is_secret_key(k) and isinstance(v, str) and v else _mask_secrets(v))
            for k, v in data.items()
        }
    return data


def _restore_secrets(data: Any, current: Any) -> None:
    """把视图保存回的掩码占位符替换为当前真实值,避免掩码落盘覆盖密钥。"""
    if isinstance(data, dict) and isinstance(current, dict):
        for k, v in data.items():
            if v == _SECRET_MASK and isinstance(current.get(k), str) and current[k]:
                data[k] = current[k]
            else:
                _restore_secrets(v, current.get(k))

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
    def __init__(
        self,
        config: Config,
        host: str = "127.0.0.1",
        port: int = 8080,
        deps: dict | None = None,
        config_path: str | Path | None = None,
    ):
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
        # 可读写配置:优先用启动时的 -c 路径,避免自定义路径下编辑失效
        self._config_path = Path(config_path) if config_path else Path(config._path or ROOT_DIR / "src" / "config.yaml")
        self._setup_auth()
        self._setup_routes()

    def _prune_tokens(self) -> None:
        now = time.time()
        expired = [t for t, exp in self._tokens.items() if exp < now]
        for t in expired:
            self._tokens.pop(t, None)

    def _setup_auth(self) -> None:
        ph = self._config.get("webui.password_hash", "")
        pw = self._config.get("webui.password", "")
        if ph:
            self._password_hash = str(ph)
        elif pw:
            self._password = str(pw)
            self._password_hash = _pbkdf2_hash(self._password)
        else:
            self._password = secrets.token_urlsafe(8)
            self._password_hash = _pbkdf2_hash(self._password)
            # 安全:随机口令仅打印到控制台(stderr),不写入轮转日志文件
            import sys

            print(f"\n[WebUI] 未配置密码,本次随机口令: {self._password}\n", file=sys.stderr)
            logger.warning("WebUI 未配置密码,已随机生成(口令见控制台,仅本次有效)")
        # 登录限流:每 IP 失败计数,超过阈值锁定 60s
        self._login_fails: dict[str, list[float]] = {}
        self._login_lockout = 60
        self._login_max_fails = 5

    def _setup_routes(self) -> None:
        self._app.router.add_get("/", self._index)
        self._app.router.add_post("/api/login", self._login)
        self._app.router.add_get("/api/status", self._status)
        self._app.router.add_get("/api/tools", self._tools)
        self._app.router.add_get("/api/tasks", self._tasks)
        self._app.router.add_post("/api/tasks", self._tasks_add)
        self._app.router.add_delete("/api/tasks", self._tasks_delete)
        self._app.router.add_get("/api/tasks/history", self._tasks_history)
        self._app.router.add_get("/api/knowledge", self._knowledge)
        self._app.router.add_post("/api/knowledge", self._knowledge_add)
        self._app.router.add_delete("/api/knowledge", self._knowledge_delete)
        self._app.router.add_post("/api/knowledge/search", self._knowledge_search)
        self._app.router.add_get("/api/providers", self._providers)
        self._app.router.add_post("/api/providers/test", self._providers_test)
        self._app.router.add_get("/api/orchestrator", self._orchestrator)
        self._app.router.add_get("/api/mcp", self._mcp_status)
        self._app.router.add_get("/api/skills", self._skills)
        self._app.router.add_get("/api/audit", self._audit)
        self._app.router.add_get("/api/subagents", self._subagents)
        self._app.router.add_post("/api/broadcast", self._broadcast)
        self._app.router.add_get("/api/plugins", self._plugins_list)
        self._app.router.add_post("/api/plugins/reload", self._plugins_reload)
        self._app.router.add_get("/api/market", self._market_list)
        self._app.router.add_post("/api/market/install", self._market_install)
        self._app.router.add_get("/api/config", self._config_view)
        self._app.router.add_post("/api/config", self._config_save)
        self._app.router.add_get("/api/adapters", self._adapters_list)
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
        self._prune_tokens()
        token = request.headers.get("Authorization", "").removeprefix("Bearer ")
        expire = self._tokens.get(token, 0)
        if expire < time.time():
            return False
        return True

    def _login_locked(self, request: web.Request) -> bool:
        """登录限流:每 IP 最近窗口内失败达阈值则锁定。"""
        ip = request.remote or request.headers.get("X-Forwarded-For", "") or "?"
        now = time.time()
        fails = [t for t in self._login_fails.get(ip, []) if now - t < self._login_lockout]
        self._login_fails[ip] = fails
        return len(fails) >= self._login_max_fails

    async def _login(self, request: web.Request) -> web.Response:
        try:
            data = await request.json()
        except json.JSONDecodeError:
            return web.json_response({"ok": False, "error": "无效请求"}, status=400)
        if self._login_locked(request):
            return web.json_response({"ok": False, "error": "尝试次数过多,请稍后再试"}, status=429)
        password = data.get("password", "")
        if not isinstance(password, str):
            return web.json_response({"ok": False, "error": "密码格式错误"}, status=400)
        if self._password:
            ok = secrets.compare_digest(password, self._password)
        else:
            ok = _verify_pbkdf2(password, self._password_hash)
        if not ok:
            ip = request.remote or request.headers.get("X-Forwarded-For", "") or "?"
            self._login_fails.setdefault(ip, []).append(time.time())
            return web.json_response({"ok": False, "error": "密码错误"}, status=401)
        self._prune_tokens()
        token = secrets.token_hex(32)
        self._tokens[token] = time.time() + 24 * 3600
        return web.json_response({"ok": True, "token": token})

    # ---------- 页面 ----------

    async def _index(self, request: web.Request) -> web.Response:
        index = STATIC_DIR / "index.html"

        def _read() -> str:
            return index.read_text(encoding="utf-8") if index.exists() else "WebUI"

        content = await asyncio.to_thread(_read)
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
                "model": stats.get("model") or self._config.get("llm.provider.model", ""),
                "provider": stats.get("provider") or self._config.get("llm.provider.type", ""),
                "tools_count": len(tools.names()) if tools else 0,
                "messages_processed": stats.get("messages_processed", 0),
                "usage": stats.get("usage", {}),  # LLM 用量/成本统计(UsageTracker.summary)
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
        audit = await asyncio.to_thread(_tail_jsonl, audit_path, 200)
        return web.json_response({"ok": True, "audit": audit})

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
            # 广播文本按发送边界转义,防管理员输入中包含的 [ 被解析为 CQ 码
            await adapter.send_group_msg(group_id, escape_cq(text))
        except Exception as exc:  # noqa: BLE001
            logger.exception("WebUI 广播失败")
            return web.json_response({"ok": False, "error": str(exc)}, status=500)
        return web.json_response({"ok": True, "group_id": group_id})

    # ---------- 插件管理 ----------

    async def _plugins_list(self, request: web.Request) -> web.Response:
        if not self._check_auth(request):
            return web.json_response({"ok": False, "error": "未授权"}, status=401)
        registry = self._deps.get("plugin_registry")
        if registry is None:
            return web.json_response({"ok": True, "plugins": []})
        meta = registry.plugin_metadata()
        plugins = []
        for stem, m in sorted(meta.items()):
            plugins.append({
                "name": m.get("name") or stem,
                "version": m.get("version", ""),
                "description": m.get("description", ""),
                "commands": m.get("commands", []),
                "dependencies": m.get("dependencies", []),
            })
        return web.json_response({
            "ok": True,
            "plugins": plugins,
            "handler_count": registry.handler_count(),
        })

    # ---------- 配置编辑 ----------

    async def _config_view(self, request: web.Request) -> web.Response:
        if not self._check_auth(request):
            return web.json_response({"ok": False, "error": "未授权"}, status=401)
        try:
            content = await asyncio.to_thread(
                lambda: self._config_path.read_text(encoding="utf-8") if self._config_path.is_file() else ""
            )
        except OSError:
            content = ""
        # 视图掩码敏感项:api_key/app_secret/sign_secret/token/password 等不暴露明文
        try:
            data = yaml.safe_load(content)
        except yaml.YAMLError:
            data = None
        if isinstance(data, dict):
            content = yaml.safe_dump(_mask_secrets(data), allow_unicode=True, sort_keys=False)
        return web.json_response({"ok": True, "content": content, "path": str(self._config_path)})

    async def _config_save(self, request: web.Request) -> web.Response:
        if not self._check_auth(request):
            return web.json_response({"ok": False, "error": "未授权"}, status=401)
        try:
            data = await request.json()
        except json.JSONDecodeError:
            return web.json_response({"ok": False, "error": "无效 JSON"}, status=400)
        content = str(data.get("content", "") or "")
        if not content.strip():
            return web.json_response({"ok": False, "error": "内容不能为空"}, status=400)
        # 解析 + schema 校验:不合法一律不落盘
        try:
            new_data = yaml.safe_load(content)
        except yaml.YAMLError as exc:
            return web.json_response({"ok": False, "error": f"YAML 解析失败: {exc}"}, status=400)
        if not isinstance(new_data, dict):
            return web.json_response({"ok": False, "error": "配置根节点必须是映射"}, status=400)
        errors = validate_config(new_data)
        if errors:
            return web.json_response(
                {"ok": False, "error": "配置校验失败", "errors": errors[:20]}, status=400
            )
        # 视图里的掩码占位符不落盘:回填"磁盘原文"(未解析环境变量的值),
        # 否则会把 ${ENV_VAR} 引用的密钥明文烤进 YAML 并落盘。
        # 磁盘原文读取失败时宁可中止保存,也不能让掩码字面量覆盖密钥。
        try:
            raw_content = (
                self._config_path.read_text(encoding="utf-8")
                if self._config_path.is_file()
                else ""
            )
            current_raw = yaml.safe_load(raw_content)
        except (OSError, yaml.YAMLError) as exc:
            logger.warning("配置保存前读取磁盘原文失败,中止: {}", exc)
            return web.json_response(
                {"ok": False, "error": f"无法读取当前配置原文,已中止保存(防密钥被覆盖): {exc}"},
                status=500,
            )
        if not isinstance(current_raw, dict):
            return web.json_response(
                {"ok": False, "error": "当前配置原文解析失败,已中止保存"}, status=500
            )
        _restore_secrets(new_data, current_raw)
        try:
            def _write() -> tuple[str, list[str]]:
                backup = self._config_path.read_text(encoding="utf-8")[:200] if self._config_path.is_file() else ""
                save_config(self._config_path, new_data)
                return backup, self._config.reload()

            backup, reload_errors = await asyncio.to_thread(_write)
            logger.info("WebUI 已更新配置文件(校验通过,{} 条重载告警)", len(reload_errors))
        except OSError as exc:
            logger.exception("配置文件写入失败")
            return web.json_response({"ok": False, "error": str(exc)}, status=500)
        return web.json_response({"ok": True, "backup_preview": backup, "warnings": reload_errors[:20]})

    # ---------- 适配器状态 ----------

    async def _adapters_list(self, request: web.Request) -> web.Response:
        if not self._check_auth(request):
            return web.json_response({"ok": False, "error": "未授权"}, status=401)
        registry = self._deps.get("adapter_registry")
        if registry is None:
            return web.json_response({"ok": True, "adapters": []})
        adapters = []
        for name in registry.names():
            adapter = registry.get(name)
            adapters.append({
                "name": name,
                "platform": getattr(adapter, "platform", ""),
                "type": type(adapter).__name__,
                "connected": bool(getattr(adapter, "_active_ws", None) or getattr(adapter, "_connections", None)),
            })
        return web.json_response({"ok": True, "adapters": adapters})

    # ---------- 知识库管理 ----------

    def _knowledge_manager(self):
        engine = self._deps.get("engine")
        return getattr(engine, "knowledge", None) if engine else None

    async def _knowledge(self, request: web.Request) -> web.Response:
        if not self._check_auth(request):
            return web.json_response({"ok": False, "error": "未授权"}, status=401)
        km = self._knowledge_manager()
        if km is None:
            return web.json_response({"ok": True, "stats": None, "docs": [], "total": 0})
        try:
            limit = _safe_int(request.query.get("limit"), 20, lo=1, hi=200)
            offset = _safe_int(request.query.get("offset"), 0, lo=0)
            docs = sorted(km._docs or [], key=lambda d: d.get("created_at", 0), reverse=True)
            page = [
                {
                    "doc_id": d["id"],
                    "title": d.get("title", ""),
                    "category": d.get("category", ""),
                    "chunks": len(d.get("chunk_ids", [])),
                    "created_at": d.get("created_at", 0),
                }
                for d in docs[offset : offset + limit]
            ]
            return web.json_response({"ok": True, "stats": km.stats(), "docs": page, "total": len(docs)})
        except Exception as exc:  # noqa: BLE001
            logger.exception("知识库列表失败")
            return web.json_response({"ok": False, "error": str(exc)}, status=500)

    async def _knowledge_add(self, request: web.Request) -> web.Response:
        if not self._check_auth(request):
            return web.json_response({"ok": False, "error": "未授权"}, status=401)
        km = self._knowledge_manager()
        if km is None:
            return web.json_response({"ok": False, "error": "知识库未启用"}, status=500)
        try:
            data = await request.json()
        except json.JSONDecodeError:
            return web.json_response({"ok": False, "error": "无效请求"}, status=400)
        content = str(data.get("content", "") or "")
        url = str(data.get("url", "") or "")
        file = str(data.get("file", "") or "")
        source = ""
        try:
            if url and not content:
                from ..agent.knowledge.readers import ContentReader

                content = await ContentReader().read_url(url)
                source = f"url:{url}"
            elif file and not content:
                from ..agent.knowledge.readers import ContentReader

                content = await ContentReader().read_file(file)
                source = f"file:{file}"
        except Exception as exc:  # noqa: BLE001
            return web.json_response({"ok": False, "error": f"内容导入失败: {exc}"}, status=400)
        if not content.strip():
            return web.json_response({"ok": False, "error": "内容为空"}, status=400)
        try:
            result = await km.add_document(
                title=str(data.get("title", "") or content[:30]),
                content=content,
                category=str(data.get("category", "通用") or "通用"),
                source=source,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("知识库添加入库失败")
            return web.json_response({"ok": False, "error": str(exc)}, status=500)
        return web.json_response({"ok": True, "result": result})

    async def _knowledge_delete(self, request: web.Request) -> web.Response:
        if not self._check_auth(request):
            return web.json_response({"ok": False, "error": "未授权"}, status=401)
        km = self._knowledge_manager()
        if km is None:
            return web.json_response({"ok": False, "error": "知识库未启用"}, status=500)
        try:
            data = await request.json()
        except json.JSONDecodeError:
            return web.json_response({"ok": False, "error": "无效请求"}, status=400)
        result = await km.delete_document(str(data.get("doc_id", "") or ""))
        return web.json_response({"ok": result.get("ok", False), "result": result})

    async def _knowledge_search(self, request: web.Request) -> web.Response:
        if not self._check_auth(request):
            return web.json_response({"ok": False, "error": "未授权"}, status=401)
        km = self._knowledge_manager()
        if km is None:
            return web.json_response({"ok": False, "error": "知识库未启用"}, status=500)
        try:
            data = await request.json()
        except json.JSONDecodeError:
            return web.json_response({"ok": False, "error": "无效请求"}, status=400)
        query = str(data.get("query", "") or "")
        if not query:
            return web.json_response({"ok": False, "error": "检索词为空"}, status=400)
        result = await km.search(
            query,
            str(data.get("category", "") or "") or None,
            _safe_int(data.get("limit"), 3, lo=1, hi=50),
        )
        return web.json_response({"ok": True, "result": result})

    # ---------- 提供商状态与测试 ----------

    def _provider_collection(self) -> list[dict]:
        """汇总 LLM/STT/TTS/Embedding/Rerank 各 Provider 状态。"""
        engine = self._deps.get("engine")
        if engine is None:
            return []
        out: list[dict] = []
        provider = getattr(engine, "provider", None)
        if provider is not None:
            out.append({
                "key": "llm", "name": "LLM 对话", "type": type(provider).__name__,
                "model": getattr(provider, "model", ""), "test": provider.test,
            })
        perc = getattr(engine, "perception", None)
        stt = getattr(getattr(perc, "audio", None), "stt_provider", None)
        if stt is not None:
            out.append({
                "key": "stt", "name": "语音识别 STT", "type": type(stt).__name__,
                "model": getattr(stt, "model", ""), "test": stt.test,
            })
        gen = getattr(engine, "generation", None)
        tts = getattr(getattr(gen, "audio", None), "tts_provider", None)
        if tts is not None:
            out.append({
                "key": "tts", "name": "语音合成 TTS", "type": type(tts).__name__,
                "model": getattr(tts, "model", ""), "test": tts.test,
            })
        km = self._knowledge_manager()
        if km is not None:
            emb = km.embedding
            if emb is not None:
                out.append({
                    "key": "embedding", "name": "向量化 Embedding", "type": type(emb).__name__,
                    "model": getattr(emb, "model", ""), "test": emb.test,
                })
            rk = km.reranker.provider if getattr(km, "reranker", None) else None
            if rk is not None:
                out.append({
                    "key": "rerank", "name": "重排序 Rerank", "type": type(rk).__name__,
                    "model": getattr(rk, "model", ""), "test": rk.test,
                })
        return out

    async def _providers(self, request: web.Request) -> web.Response:
        if not self._check_auth(request):
            return web.json_response({"ok": False, "error": "未授权"}, status=401)
        items = []
        for p in self._provider_collection():
            items.append({"key": p["key"], "name": p["name"], "type": p["type"], "model": p["model"]})
        return web.json_response({"ok": True, "providers": items})

    async def _providers_test(self, request: web.Request) -> web.Response:
        if not self._check_auth(request):
            return web.json_response({"ok": False, "error": "未授权"}, status=401)
        results = []
        for p in self._provider_collection():
            try:
                ok = bool(await p["test"]())
                results.append({"key": p["key"], "name": p["name"], "ok": ok})
            except Exception as exc:  # noqa: BLE001
                results.append({"key": p["key"], "name": p["name"], "ok": False, "error": str(exc)})
        return web.json_response({"ok": True, "results": results})

    # ---------- 定时任务 CRUD ----------

    async def _orchestrator(self, request: web.Request) -> web.Response:
        if not self._check_auth(request):
            return web.json_response({"ok": False, "error": "未授权"}, status=401)
        orch = self._deps.get("orchestrator")
        return web.json_response({"ok": True, "summary": orch.summary() if orch else {"providers": 0}})

    async def _tasks_add(self, request: web.Request) -> web.Response:
        if not self._check_auth(request):
            return web.json_response({"ok": False, "error": "未授权"}, status=401)
        cron = self._deps.get("cron")
        if cron is None:
            return web.json_response({"ok": False, "error": "定时模块未启用"}, status=500)
        try:
            data = await request.json()
        except json.JSONDecodeError:
            return web.json_response({"ok": False, "error": "无效请求"}, status=400)
        when = str(data.get("when", "") or "")
        text = str(data.get("text", "") or "")
        if not when or not text:
            return web.json_response({"ok": False, "error": "需要 when(触发时间) 和 text(内容)"}, status=400)
        result = await cron.add_task(
            session_id="webui",
            when=when,
            text=text,
            target_group=str(data.get("target_group", "") or "") or None,
            target_user=str(data.get("target_user", "") or "") or None,
        )
        return web.json_response({"ok": "ok" in result, "result": result})

    async def _tasks_delete(self, request: web.Request) -> web.Response:
        if not self._check_auth(request):
            return web.json_response({"ok": False, "error": "未授权"}, status=401)
        cron = self._deps.get("cron")
        if cron is None:
            return web.json_response({"ok": False, "error": "定时模块未启用"}, status=500)
        try:
            data = await request.json()
        except json.JSONDecodeError:
            return web.json_response({"ok": False, "error": "无效请求"}, status=400)
        result = cron.delete_task("webui", str(data.get("task_id", "") or ""))
        return web.json_response({"ok": result.get("ok", False), "result": result})

    async def _tasks_history(self, request: web.Request) -> web.Response:
        if not self._check_auth(request):
            return web.json_response({"ok": False, "error": "未授权"}, status=401)
        cron = self._deps.get("cron")
        history = cron.get_history(50) if cron else []
        return web.json_response({"ok": True, "history": history})

    # ---------- 插件重载 ----------

    async def _market_list(self, request: web.Request) -> web.Response:
        if not self._check_auth(request):
            return web.json_response({"ok": False, "error": "未授权"}, status=401)
        market = self._deps.get("plugin_market")
        plugins = await market.available(refresh=True) if market else []
        return web.json_response({"ok": True, "plugins": plugins})

    async def _market_install(self, request: web.Request) -> web.Response:
        if not self._check_auth(request):
            return web.json_response({"ok": False, "error": "未授权"}, status=401)
        market = self._deps.get("plugin_market")
        if market is None:
            return web.json_response({"ok": False, "error": "插件市场未启用"}, status=500)
        try:
            data = await request.json()
        except json.JSONDecodeError:
            return web.json_response({"ok": False, "error": "无效请求"}, status=400)
        result = await market.install(str(data.get("name", "") or ""))
        # 安装成功后热重载插件
        if result.get("ok"):
            registry = self._deps.get("plugin_registry")
            if registry is not None:
                from ..main import _plugin_dirs

                for plugin_dir in _plugin_dirs():
                    await registry.reload_from_directory(plugin_dir)
        return web.json_response(result)

    async def _plugins_reload(self, request: web.Request) -> web.Response:
        if not self._check_auth(request):
            return web.json_response({"ok": False, "error": "未授权"}, status=401)
        registry = self._deps.get("plugin_registry")
        if registry is None:
            return web.json_response({"ok": False, "error": "插件模块未启用"}, status=500)
        try:
            from ..main import _plugin_dirs

            # reload_from_directory 内部会先 unload_external,多目录时先统一卸载
            # 再逐个加载,避免后一个目录卸载掉前一个目录刚加载的插件。
            registry.unload_external()
            for plugin_dir in _plugin_dirs():
                await registry.load_from_directory(plugin_dir)
            return web.json_response(
                {"ok": True, "plugins": len(registry.plugin_metadata()), "handlers": registry.handler_count()}
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("插件重载失败")
            return web.json_response({"ok": False, "error": str(exc)}, status=500)

    # ---------- 聊天 ----------

    async def _chat_ws(self, request: web.Request) -> web.WebSocketResponse:
        # 浏览器 WebSocket 无法携带 Authorization 头,改用子协议传递 token:
        # 前端 new WebSocket(url, ["chat", "auth.<token>"])
        self._prune_tokens()
        proto = request.headers.get("Sec-WebSocket-Protocol", "")
        token = ""
        for part in proto.split(","):
            part = part.strip()
            if part.startswith("auth."):
                token = part[len("auth.") :]
        if not token or self._tokens.get(token, 0) < time.time():
            return web.Response(status=401, text="Unauthorized")
        ws = web.WebSocketResponse(protocols=["chat"])
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
            # 安全:user_id 加 webui_ 前缀并清洗,防止客户端伪造真实 QQ 号冒充管理员。
            # 引擎按 user_id 计算角色等级,"webui_xxx" 永不匹配 admin/超管 QQ。
            raw_uid = str(data.get("user_id", "webui") or "webui")
            user_id = "webui_" + re.sub(r"[^A-Za-z0-9_\-]", "_", raw_uid)[:32]
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
                    # event.reply 通道同时承载进度提示(思考中/工具执行),标记为 progress 便于前端区分
                    await ws.send_str(json.dumps({"role": "progress", "text": _text}))

            async def _stream(_content: str, _reasoning: str) -> None:
                # LLM 流式 delta:WebUI 逐 token 展示;思考增量走 reasoning 通道
                if _content and not ws.closed:
                    await ws.send_str(json.dumps({"role": "stream", "text": _content}))
                elif _reasoning and not ws.closed:
                    await ws.send_str(json.dumps({"role": "reasoning", "text": _reasoning}))

            event._stream_callback = _stream

            event._send_callback = _cb
            try:
                reply = await engine.process(event)
            except Exception:  # noqa: BLE001
                logger.exception("WebUI 聊天处理异常")
                reply = "处理出错,请查看服务端日志。"
            if reply:
                try:
                    # closed 检查与 send_str 之间存在断线竞态,这里兜底
                    await ws.send_str(json.dumps({"role": "assistant", "text": reply}))
                except (RuntimeError, ConnectionError):
                    return ws
        return ws
