"""ChatService(本项目适配):WebUI 聊天直接调用本项目 AgentEngine。

将 AstrBot 前端聊天协议(SSE 流 + 会话 API)映射到本项目引擎:
- ``POST /api/v1/chat`` → SSE 流,内部调用 ``AgentEngine.process(event)``
- 会话/线程/文件等 API 基于内存会话表与临时文件
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

from astrbot.core import logger
from astrbot.core.core_lifecycle import AstrBotCoreLifecycle
from astrbot.core.utils.astrbot_path import get_astrbot_data_path, get_astrbot_temp_path


class ChatServiceError(Exception):
    pass


def _sse(data_obj: Any) -> str:
    return f"data: {json.dumps(data_obj, ensure_ascii=False)}\n\n"


def _text_of(message: Any) -> str:
    """从 AstrBot 消息格式(message 字符串或 parts 列表)提取纯文本。"""
    if isinstance(message, list):
        parts: list[str] = []
        for p in message:
            if isinstance(p, dict):
                parts.append(str(p.get("text", "") or p.get("content", "") or ""))
            else:
                parts.append(str(p))
        return "".join(parts).strip()
    return str(message or "").strip()


def collect_plain_text_from_message_parts(parts) -> str:
    """从消息 parts 提取纯文本(live_chat_service 兼容)。"""
    return _text_of(parts)


def extract_web_search_refs(parts, **kwargs) -> list:
    """提取搜索引用(本项目简化:返回空)。"""
    return []


def build_bot_history_content(messages, **kwargs) -> dict:
    """构建机器人历史消息内容(live_chat_service 兼容)。"""
    return {"type": "bot", "message": list(messages or [])}


class BotMessageAccumulator:
    """机器人消息累积器(live_chat_service 兼容 stub)。"""

    def __init__(self) -> None:
        self.parts: list[dict] = []

    def add_plain(self, text: str) -> None:
        self.parts.append({"type": "plain", "text": text})

    def add_attachment(self, part: dict | None) -> None:
        if part:
            self.parts.append(part)

    def plain_text(self) -> str:
        return "".join(p.get("text", "") for p in self.parts if p.get("type") == "plain")


class ChatService:
    def __init__(self, db, core_lifecycle: AstrBotCoreLifecycle) -> None:
        self.db = db
        self.lifecycle = core_lifecycle
        self.engine = getattr(core_lifecycle, "_engine", None)
        # (username, session_id) -> 会话元数据
        self._sessions: dict[tuple[str, str], dict] = {}
        self._chat_runs: dict[str, dict] = {}
        self._threads: dict[str, dict] = {}
        self._order = 0

    # ================= 会话 CRUD =================

    def _session_key(self, username: str, session_id: str) -> tuple[str, str]:
        return (username, session_id)

    def _session_payload(self, s: dict) -> dict:
        return {
            "session_id": s.get("session_id"),
            "display_name": s.get("display_name", "新会话"),
            "platform_id": "webchat",
            "created_at": s.get("created_at"),
            "updated_at": s.get("updated_at"),
            "last_message": s.get("last_message", ""),
            "message_count": s.get("message_count", 0),
        }

    async def new_session(self, username: str, platform_id: str = "webchat") -> dict:
        sid = str(uuid.uuid4())
        now = time.time()
        self._order += 1
        s = {
            "session_id": sid,
            "display_name": "新会话",
            "created_at": now,
            "updated_at": now,
            "last_message": "",
            "message_count": 0,
            "order": self._order,
        }
        self._sessions[self._session_key(username, sid)] = s
        return self._session_payload(s)

    async def get_sessions(self, username: str, platform_id: str | None = None) -> list[dict]:
        items = [
            s for (u, _sid), s in self._sessions.items() if u == username
        ]
        items.sort(key=lambda s: s.get("order", 0), reverse=True)
        return [self._session_payload(s) for s in items]

    async def get_session(self, username: str, session_id: str) -> dict:
        s = self._sessions.get(self._session_key(username, session_id))
        if not s:
            raise ChatServiceError("会话不存在")
        return self._session_payload(s)

    async def get_session_from_dashboard_query(self, username: str, session_id: str) -> dict:
        return await self.get_session(username, session_id)

    async def update_session_display_name(
        self, username: str, session_id: str, display_name: str
    ) -> dict:
        s = self._sessions.get(self._session_key(username, session_id))
        if not s:
            raise ChatServiceError("会话不存在")
        s["display_name"] = display_name or s.get("display_name", "新会话")
        s["updated_at"] = time.time()
        return self._session_payload(s)

    async def update_session_display_name_from_dashboard_payload(
        self, username: str, data: dict
    ) -> dict:
        return await self.update_session_display_name(
            username, data.get("session_id"), data.get("display_name", "")
        )

    async def delete_webchat_session(self, username: str, session_id: str) -> None:
        self._sessions.pop(self._session_key(username, session_id), None)

    async def delete_webchat_session_from_dashboard_query(
        self, username: str, session_id: str
    ) -> None:
        await self.delete_webchat_session(username, session_id)

    async def batch_delete_sessions(self, username: str, data: dict) -> dict:
        ids = data.get("session_ids") or data.get("session_id") or []
        if isinstance(ids, str):
            ids = [ids]
        for sid in ids:
            self._sessions.pop(self._session_key(username, sid), None)
        return {"deleted": len(ids)}

    async def batch_delete_sessions_from_dashboard_payload(
        self, username: str, data: dict
    ) -> dict:
        return await self.batch_delete_sessions(username, data)

    # ================= 停止/重新生成 =================

    async def stop_session(self, username: str, session_id: str) -> dict:
        for run in self._chat_runs.values():
            if run.get("username") == username and run.get("session_id") == session_id:
                task = run.get("task")
                if task and not task.done():
                    task.cancel()
        return {"stopped": True}

    async def stop_session_from_dashboard_payload(
        self, username: str, data: dict
    ) -> dict:
        return await self.stop_session(username, data.get("session_id"))

    async def update_message(self, username: str, data: dict) -> dict:
        """编辑历史消息(本项目引擎上下文不由 WebUI 直接管理,返回成功)。"""
        return {"ok": True, "message_id": data.get("message_id")}

    async def prepare_regenerate_message_payload(self, username: str, data: dict) -> dict:
        payload = dict(data)
        payload.setdefault("message", payload.get("message", ""))
        return payload

    async def prepare_regenerate_message_payload_from_dashboard_payload(
        self, username: str, data: dict
    ) -> dict:
        return await self.prepare_regenerate_message_payload(username, data)

    # ================= 线程(子会话) =================

    async def create_thread(self, username: str, data: dict) -> dict:
        tid = str(uuid.uuid4())
        self._threads[tid] = {
            "thread_id": tid,
            "username": username,
            "session_id": data.get("session_id"),
            "selected_text": data.get("selected_text", ""),
            "created_at": time.time(),
        }
        return self._threads[tid]

    async def get_thread(self, username: str, thread_id: str) -> dict:
        t = self._threads.get(thread_id)
        if not t or t.get("username") != username:
            raise ChatServiceError("线程不存在")
        return t

    async def get_thread_from_dashboard_query(self, username: str, thread_id: str) -> dict:
        return await self.get_thread(username, thread_id)

    async def delete_thread(self, username: str, thread_id: str) -> None:
        self._threads.pop(thread_id, None)

    async def delete_thread_from_dashboard_payload(self, username: str, data: dict) -> None:
        await self.delete_thread(username, data.get("thread_id"))

    async def prepare_thread_chat_payload(self, username: str, data: dict) -> dict:
        payload = dict(data)
        payload.setdefault("message", payload.get("message", ""))
        return payload

    async def prepare_thread_chat_payload_from_dashboard_payload(
        self, username: str, data: dict
    ) -> dict:
        return await self.prepare_thread_chat_payload(username, data)

    # ================= 文件 =================

    async def save_uploaded_file(self, file) -> dict:
        raw = await file.read()
        fname = getattr(file, "filename", "untitled") or "untitled"
        safe = re.sub(r"[^A-Za-z0-9._\-一-鿿]", "_", fname)
        tmp_dir = os.path.join(get_astrbot_temp_path(), "webchat")
        os.makedirs(tmp_dir, exist_ok=True)
        dest = os.path.join(tmp_dir, f"{uuid.uuid4().hex}_{safe}")
        with open(dest, "wb") as f:
            f.write(raw)
        mime = getattr(file, "content_type", None) or "application/octet-stream"
        return {
            "filename": fname,
            "path": dest,
            "mime_type": mime,
            "attachment_id": os.path.basename(dest),
        }

    async def resolve_attachment_file(self, attachment_id: str) -> tuple[str, str]:
        tmp_dir = os.path.join(get_astrbot_temp_path(), "webchat")
        path = os.path.join(tmp_dir, os.path.basename(str(attachment_id)))
        if not os.path.exists(path):
            raise ChatServiceError("附件不存在")
        return path, "application/octet-stream"

    async def resolve_attachment_file_from_dashboard_query(self, attachment_id: str) -> tuple[str, str]:
        return await self.resolve_attachment_file(attachment_id)

    async def resolve_webchat_file(self, filename: str) -> tuple[str, str]:
        raise ChatServiceError("文件不存在")

    async def resolve_webchat_file_from_dashboard_query(self, filename: str) -> tuple[str, str]:
        return await self.resolve_webchat_file(filename)

    # ================= 消息构建(兼容签名) =================

    async def build_user_message_parts(self, message: str | list) -> list[dict]:
        return [{"type": "plain", "text": _text_of(message)}]

    # ================= 流式聊天(核心) =================

    async def build_chat_stream(
        self,
        username: str,
        post_data: dict,
    ) -> AsyncIterator[str]:
        """SSE 聊天流:async def 返回内部 async generator(与 api 层 await 契约一致)。"""
        message = _text_of(post_data.get("message", ""))
        if not message:
            raise ChatServiceError("消息为空")
        session_id = post_data.get("session_id") or post_data.get("conversation_id") or "default"
        run_id = str(uuid.uuid4())
        queue: asyncio.Queue = asyncio.Queue()

        async def _stream() -> AsyncIterator[str]:
            yield _sse({"type": "run_started", "data": {"run_id": run_id, "session_id": session_id, "platform_id": "webchat"}})
            yield _sse({"type": "session_id", "data": session_id})
            yield _sse({"type": "user_message_saved", "data": {"id": run_id, "llm_checkpoint_id": run_id}})
            yield _sse({"type": "message_saved", "data": {"id": run_id, "llm_checkpoint_id": run_id}})

            if self.engine is None:
                yield _sse({"type": "complete", "data": "引擎未配置,无法聊天。"})
                yield _sse({"type": "end"})
                return

            # 构造 AgentEvent(参考旧 aiohttp WebUI 的 /ws/chat)
            safe_user = "webui_" + re.sub(r"[^A-Za-z0-9_\-]", "_", str(username))[:32]
            from src.adapter.event import AgentEvent
            from src.adapter.message import MessageChain, MessageSegment

            event = AgentEvent(
                message_type="private",
                user_id=safe_user,
                sender_name=safe_user,
                session_id=f"webui:{safe_user}:{session_id}",
                message=MessageChain([MessageSegment.text(message)]),
                is_tome=True,
            )

            def _stream_cb(content: str, reasoning: str) -> None:
                if content:
                    queue.put_nowait({"type": "plain", "data": content, "streaming": True})
                elif reasoning:
                    queue.put_nowait({"type": "plain", "chain_type": "reasoning", "data": reasoning})

            def _tool_cb(name: str, args: dict, result: str) -> None:
                queue.put_nowait({
                    "type": "plain",
                    "chain_type": "tool_call",
                    "data": json.dumps({"name": name, "arguments": args}, ensure_ascii=False),
                })
                queue.put_nowait({
                    "type": "plain",
                    "chain_type": "tool_call_result",
                    "data": json.dumps({"name": name, "result": result}, ensure_ascii=False),
                })

            def _cb(_event, _text: str, at: bool = False) -> None:
                # 进度提示忽略(避免与最终回复叠加)
                pass

            event._stream_callback = _stream_cb
            event._tool_callback = _tool_cb
            event._send_callback = _cb

            self._chat_runs[run_id] = {"username": username, "session_id": session_id}

            async def _run() -> None:
                try:
                    reply = await self.engine.process(event)
                    queue.put_nowait({"type": "__final__", "data": reply or ""})
                except asyncio.CancelledError:
                    queue.put_nowait({"type": "complete", "data": "\n\n(已停止)"})
                    queue.put_nowait({"type": "__final__", "data": ""})
                except Exception as exc:  # noqa: BLE001
                    logger.error("WebUI 聊天处理异常: %s", exc, exc_info=True)
                    queue.put_nowait({"type": "error", "data": "处理出错,请查看服务端日志。"})
                    queue.put_nowait({"type": "__final__", "data": ""})

            task = asyncio.create_task(_run(), name=f"webchat_run_{run_id}")
            self._chat_runs[run_id]["task"] = task

            final_text = ""
            try:
                while True:
                    item = await queue.get()
                    t = item.get("type")
                    if t == "__final__":
                        complete_text = item.get("data", "") or final_text
                        yield _sse({"type": "complete", "data": complete_text})
                        yield _sse({"type": "end"})
                        break
                    if t == "plain" and item.get("chain_type") not in (
                        "reasoning",
                        "tool_call",
                        "tool_call_result",
                    ):
                        final_text += item.get("data", "")
                    yield _sse(item)
            finally:
                if not task.done():
                    task.cancel()
                self._chat_runs.pop(run_id, None)

        return _stream()

    async def build_chat_run_stream(
        self,
        username: str,
        run_id: str,
    ) -> AsyncIterator[str]:
        run = self._chat_runs.get(run_id)
        if run is None:
            raise ChatServiceError(f"Chat run {run_id} not found")
        if run.get("username") != username:
            raise ChatServiceError("Permission denied")
        yield _sse({"type": "complete", "data": ""})
        yield _sse({"type": "end"})
