"""统一 Webhook 服务器:单端口多平台事件路由(参照 AstrBot 统一消息网关)。

- POST /webhook/<platform>: 各平台把事件上报到这里
- 事件按 {platform, event_type, ...} 规范化后交给回调(通常为管道 execute)
- 内置鉴权 token(可选)
"""
from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

from aiohttp import web
from loguru import logger

from .event import AgentEvent
from .message import MessageChain, MessageSegment


class WebhookServer:
    """统一 webhook 入口。"""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 6196,
        path: str = "/webhook",
        token: str = "",
        callback: Callable[[AgentEvent], Awaitable[None]] | None = None,
    ):
        self._host = host
        self._port = int(port)
        self._path = path
        self._token = token
        self._callback = callback
        self._app = web.Application()
        self._app.router.add_post(f"{path}/{{platform}}", self._handle)
        self._runner: web.AppRunner | None = None

    def set_callback(self, cb: Callable[[AgentEvent], Awaitable[None]]) -> None:
        self._callback = cb

    async def _handle(self, request: web.Request) -> web.Response:
        if self._token:
            auth = request.headers.get("Authorization", "")
            if auth != f"Bearer {self._token}":
                return web.json_response({"ok": False, "error": "未授权"}, status=401)
        platform = request.match_info.get("platform", "unknown")
        try:
            data = await request.json()
        except Exception:  # noqa: BLE001
            return web.json_response({"ok": False, "error": "无效 JSON"}, status=400)
        try:
            event = self._build_event(platform, data)
        except Exception as exc:  # noqa: BLE001
            logger.warning("webhook 事件解析失败(platform={}): {}", platform, exc)
            return web.json_response({"ok": False, "error": "事件解析失败"}, status=400)
        if self._callback is not None:
            try:
                await self._callback(event)
            except Exception:  # noqa: BLE001
                logger.exception("webhook 事件处理失败")
                return web.json_response({"ok": False, "error": "处理失败"}, status=500)
        return web.json_response({"ok": True})

    @staticmethod
    def _build_event(platform: str, data: dict) -> AgentEvent:
        """把平台上报事件规范化为 AgentEvent(消息/通知/请求)。"""
        event_type = data.get("event_type") or data.get("post_type") or "message"
        message_type = data.get("message_type") or "private"
        user_id = str(data.get("user_id", data.get("sender", {}).get("user_id", "")) or "")
        group_id = str(data.get("group_id", "") or "")
        raw = data.get("message", data.get("text", ""))
        text = data.get("text", "")
        if isinstance(raw, str):
            text = text or raw
        is_tome = bool(data.get("is_tome", False))
        event = AgentEvent(
            platform=platform,
            event_type="message" if event_type in ("message", "event") else event_type,
            message_type=message_type,
            user_id=user_id,
            group_id=group_id,
            sender_name=data.get("sender", {}).get("card", "") or data.get("sender", {}).get("nickname", "") or user_id,
            session_id=f"{platform}:{group_id or user_id}",
            message=MessageChain([MessageSegment.text(text)]),
            is_tome=is_tome,
        )
        event.state.update(data.get("state", {}) or {})
        return event

    async def start(self) -> None:
        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self._host, self._port)
        await site.start()
        logger.info("Webhook 服务器已启动: http://{}:{}{}/<platform>", self._host, self._port, self._path)

    async def stop(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None
