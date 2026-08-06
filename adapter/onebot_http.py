"""OneBot v11 HTTP 模式适配器:POST 上报接收 + HTTP API 调用。

- 事件接收:OneBot 实现端通过 HTTP POST 把事件上报到本机路径(webhook)。
- API 调用:直接对 OneBot 端的 HTTP API 端点发起 POST 请求。

适用于走 HTTP 上报而非 WebSocket 的部署(go-cqhttp http-post / Lagrange
http 上报等),无需保持长连接。
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import aiohttp
from aiohttp import web
from loguru import logger

from .base import BaseAdapter
from .driver import ReverseDriver, ReverseServerMixin
from .event import AgentEvent
from .message import MessageChain


class OneBotV11Http(ReverseServerMixin, BaseAdapter):
    platform = "qq"

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 6198,
        path: str = "/onebot",
        http_url: str = "http://127.0.0.1:5700",
        token: str = "",
        self_id: str = "",
        driver: ReverseDriver | None = None,
    ):
        self.http_url = http_url.rstrip("/")
        self.token = token
        self.self_id = self_id
        self._init_server(host, port, path, driver)
        self._session: aiohttp.ClientSession | None = None

    def _register_driver_route(self, driver: ReverseDriver, path: str) -> None:
        driver.register_http(path, self._webhook_handler, method="POST")

    def _register_routes(self, app: web.Application) -> None:
        app.router.add_post(self.path, self._webhook_handler)

    async def start(self) -> None:
        await self._start_server("OneBot v11 HTTP")
        self._session = aiohttp.ClientSession()
        logger.info(f"OneBot v11 HTTP API 端点: {self.http_url}")

    async def stop(self) -> None:
        await self._stop_server()
        if self._session is not None:
            await self._session.close()
            self._session = None
        logger.info("OneBot v11 HTTP 适配器已停止")

    # ---------- 事件上报接收 ----------

    async def _webhook_handler(self, request: web.Request) -> web.Response:
        if not self._authorized(request):
            return web.Response(status=401, text="Unauthorized")
        try:
            data = await request.json()
        except (json.JSONDecodeError, UnicodeDecodeError):
            logger.warning("HTTP 上报 JSON 解析失败")
            return web.Response(status=400, text="Invalid JSON")
        asyncio.create_task(self._dispatch_frame(data))
        return web.Response(status=200, text="OK")

    async def _dispatch_frame(self, data: dict[str, Any]) -> None:
        try:
            if data.get("post_type") == "message" and self.on_event is not None:
                event = self._build_event(data)
                await self.on_event(event)
        except Exception:  # noqa: BLE001
            logger.exception("HTTP 上报事件处理异常")

    def _build_event(self, data: dict[str, Any]) -> AgentEvent:
        mtype = data.get("message_type", "group")
        user_id = str(data.get("user_id", ""))
        group_id = str(data.get("group_id", "")) if data.get("group_id") else None
        sender = data.get("sender") or {}
        message = data.get("message", [])
        raw = data.get("raw_message", "")
        self_id = str(data.get("self_id", self.self_id))

        if isinstance(message, str):
            chain = MessageChain.from_cq_string(message)
        else:
            chain = MessageChain.from_segments(message)

        is_tome = False
        if mtype == "group":
            at_qs = [str(s.data.get("qq")) for s in chain.get("at")]
            is_tome = self_id in at_qs or "all" in at_qs

        session_id = group_id if mtype == "group" else user_id
        return AgentEvent(
            platform="qq",
            message_type=mtype,
            group_id=group_id,
            user_id=user_id,
            sender_name=sender.get("nickname", "") or str(user_id),
            sender_role=sender.get("role", ""),
            message=chain,
            raw_message=raw,
            message_id=data.get("message_id"),
            session_id=session_id,
            is_tome=is_tome,
            _send_callback=self._reply,
        )

    # ---------- 发送 ----------

    async def _reply(self, event: AgentEvent, text: str, at: bool = False) -> None:
        if not text:
            return
        if at and event.message_type == "group":
            text = f"[CQ:at,qq={event.user_id}] {text}"
        if event.message_type == "group" and event.group_id:
            await self.send_group_msg(event.group_id, text)
        else:
            await self.send_private_msg(event.user_id, text)

    async def send_message(self, event: AgentEvent, text: str, at: bool = False) -> Any:
        return await self._reply(event, text, at=at)

    # ---------- HTTP API 调用 ----------

    async def call_api(self, action: str, **params: Any) -> Any:
        if self._session is None:
            raise ConnectionError("OneBot HTTP 适配器未启动")
        url = f"{self.http_url}/{action}"
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        async with self._session.post(url, json=params, headers=headers, timeout=30) as resp:
            try:
                body = await resp.json()
            except (json.JSONDecodeError, UnicodeDecodeError):
                text = (await resp.text())[:200]
                raise RuntimeError(f"OneBot HTTP 响应非 JSON ({resp.status}): {text}")
        if isinstance(body, dict) and body.get("status") == "failed":
            raise RuntimeError(
                f"OneBot API 失败: {action} retcode={body.get('retcode')} "
                f"msg={body.get('msg') or body.get('wording')}"
            )
        return body.get("data") if isinstance(body, dict) else body
