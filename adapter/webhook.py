"""统一 Webhook 支持 — 参考 AstrBot webhook_server.py.

为以 Webhook 方式接入的平台提供统一的 HTTP 回调端点。
支持: Telegram, Discord, 飞书, 钉钉, Slack 等 webhook-based 平台。

目前先提供基础框架，具体平台实现通过插件扩展。
"""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime
from typing import Any

from aiohttp import web

from .event import AgentEvent, EventType, MessageType


class WebhookServer:
    """统一 Webhook 服务器.

    为各平台提供 HTTP POST 回调端点，将 webhook 数据
    转换为统一的 AgentEvent 后推送到消息管道。
    """

    def __init__(self, host: str = "0.0.0.0", port: int = 6186):
        self.host = host
        self.port = port
        self._app = web.Application()
        self._runner: web.AppRunner | None = None
        self._handlers: dict[str, callable] = {}
        self.on_event: callable | None = None

        # 注册通用端点
        self._app.router.add_post("/webhook/{platform}", self._handle_webhook)
        self._app.router.add_get("/health", self._handle_health)
        self._app.router.add_post("/webhook/qq_official", self._handle_qq_official)
        self._app.router.add_post("/webhook/telegram", self._handle_telegram_like)
        self._app.router.add_post("/webhook/discord", self._handle_telegram_like)

    def register_handler(self, platform: str, handler: callable) -> None:
        """注册平台特定的 webhook 处理器."""
        self._handlers[platform] = handler

    async def _handle_health(self, request: web.Request) -> web.Response:
        return web.json_response({
            "status": "ok",
            "time": datetime.now().isoformat(),
        })

    async def _handle_webhook(self, request: web.Request) -> web.Response:
        """通用 webhook 端点."""
        platform = request.match_info.get("platform", "unknown")

        try:
            body = await request.json()
        except json.JSONDecodeError:
            body = {"raw": await request.text()}

        # 平台特定处理器
        if platform in self._handlers:
            event = await self._handlers[platform](body, request.headers)
        else:
            event = self._parse_generic(body, platform)

        if event and self.on_event:
            try:
                await self.on_event(event)
            except Exception:
                from loguru import logger
                logger.exception("Webhook 事件处理失败")

        return web.json_response({"ok": True})

    async def _handle_qq_official(self, request: web.Request) -> web.Response:
        """QQ 官方 Webhook 端点."""
        body = await request.json()
        event = self._parse_qq_official(body)
        if event and self.on_event:
            await self.on_event(event)
        return web.json_response({"ok": True})

    async def _handle_telegram_like(self, request: web.Request) -> web.Response:
        """Telegram/Discord 类 webhook."""
        body = await request.json()
        event = self._parse_generic(body, "webhook")
        if event and self.on_event:
            await self.on_event(event)
        return web.json_response({"ok": True})

    def _parse_generic(self, body: dict, platform: str) -> AgentEvent | None:
        """通用 webhook 解析."""
        text = body.get("text") or body.get("content") or body.get("message", {}).get("text", "")
        user_id = str(body.get("user_id", body.get("from", {}).get("id", "unknown")))
        user_name = body.get("user_name", body.get("from", {}).get("username", ""))
        chat_id = str(body.get("chat_id", body.get("chat", {}).get("id", "")))

        if not text:
            return None

        event = AgentEvent(
            type=EventType.MESSAGE,
            message_type=MessageType.GROUP if chat_id else MessageType.PRIVATE,
            user_id=user_id,
            user_name=user_name,
            group_id=chat_id,
            message=text,
            raw_event=body,
        )
        event.session_id = event.get_session_id()
        return event

    def _parse_qq_official(self, body: dict) -> AgentEvent | None:
        """QQ 官方 Webhook 解析."""
        op = body.get("op", 0)
        if op != 0:  # C2C/群消息事件
            return None

        content = body.get("d", {}).get("content", "")
        author = body.get("d", {}).get("author", {})
        user_id = str(author.get("id", ""))
        group_id = str(body.get("d", {}).get("group_openid", ""))

        if not content:
            return None

        event = AgentEvent(
            type=EventType.MESSAGE,
            message_type=MessageType.GROUP if group_id else MessageType.PRIVATE,
            user_id=user_id,
            user_name=author.get("username", ""),
            group_id=group_id,
            message=content,
            raw_event=body,
        )
        event.session_id = event.get_session_id()
        return event

    async def start(self) -> None:
        """启动 webhook 服务器."""
        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self.host, self.port)
        await site.start()

        from loguru import logger
        logger.info(f"Webhook 服务器已启动: http://{self.host}:{self.port}")

    async def stop(self) -> None:
        if self._runner:
            await self._runner.cleanup()
