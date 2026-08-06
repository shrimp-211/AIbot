"""OneBot v11 协议 WebSocket 适配器(服务端模式)。

通过 aiohttp 提供 WS 服务端,接收 NapCat / Lagrange / go-cqhttp 等
OneBot 客户端的连接,实现全双工通信:接收消息事件 + echo 关联的
异步 API 调用(`call_api` 带 30s 超时)。
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Awaitable, Callable

from aiohttp import WSMsgType, web
from loguru import logger

from .event import AgentEvent
from .message import MessageChain

EventCallback = Callable[[AgentEvent], Awaitable[None]]


class OneBotV11Adapter:
    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 6199,
        path: str = "/ws",
        token: str = "",
        self_id: str = "",
        on_event: EventCallback | None = None,
    ):
        self.host = host
        self.port = port
        self.path = path
        self.token = token
        self.self_id = str(self_id)
        self.on_event = on_event
        self.send_callback: EventCallback | None = None

        self._app = web.Application()
        self._app.router.add_get(path, self._ws_handler)
        self._runner = web.AppRunner(self._app)
        self._site: web.TCPSite | None = None
        self._ws: web.WebSocketResponse | None = None
        self._echo_waiters: dict[str, asyncio.Future] = {}
        self._seq = 0
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, self.host, self.port)
        await self._site.start()
        logger.info(
            f"OneBot v11 WS 服务已启动: ws://{self.host}:{self.port}{self.path}"
        )

    async def stop(self) -> None:
        if self._site is not None:
            await self._site.stop()
        await self._runner.cleanup()
        logger.info("OneBot v11 WS 服务已停止")

    # ---------- WebSocket 连接 ----------

    async def _ws_handler(self, request: web.Request) -> web.WebSocketResponse:
        if self.token:
            auth = request.headers.get("Authorization", "")
            if auth != f"Bearer {self.token}":
                logger.warning("WebSocket 连接鉴权失败")
                return web.Response(status=401, text="Unauthorized")

        ws = web.WebSocketResponse(max_msg_size=16 * 1024 * 1024)
        await ws.prepare(request)
        async with self._lock:
            self._ws = ws
        logger.info("OneBot 客户端已连接")

        try:
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    try:
                        data = json.loads(msg.data)
                    except json.JSONDecodeError:
                        logger.warning(f"无效 JSON: {msg.data[:200]}")
                        continue
                    self._on_frame(data)
                elif msg.type == WSMsgType.ERROR:
                    break
        finally:
            async with self._lock:
                if self._ws is ws:
                    self._ws = None
            logger.info("OneBot 客户端已断开")
        return ws

    # ---------- 事件分发 ----------

    def _on_frame(self, data: dict[str, Any]) -> None:
        if "echo" in data and "status" in data:
            echo = str(data.get("echo"))
            fut = self._echo_waiters.pop(echo, None)
            if fut is not None and not fut.done():
                fut.set_result(data)
            return

        post_type = data.get("post_type")
        if post_type == "message":
            asyncio.create_task(self._handle_message(data))
        elif post_type == "meta_event":
            # heartbeat / lifecycle: 心跳无需处理
            pass

    async def _handle_message(self, data: dict[str, Any]) -> None:
        if self.on_event is not None:
            event = self._build_event(data)
            await self.on_event(event)

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
        event = AgentEvent(
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
            _send_callback=self.send_callback,
        )
        return event

    # ---------- 异步 API 调用 ----------

    async def call_api(self, action: str, **params: Any) -> Any:
        ws = self._ws
        if ws is None or ws.closed:
            raise ConnectionError("WebSocket 未连接")
        async with self._lock:
            self._seq += 1
            echo = str(self._seq)
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._echo_waiters[echo] = fut
        payload = {"action": action, "params": params, "echo": echo}
        await ws.send_str(json.dumps(payload, ensure_ascii=False))
        try:
            resp = await asyncio.wait_for(fut, timeout=30)
        except asyncio.TimeoutError:
            self._echo_waiters.pop(echo, None)
            raise TimeoutError(f"OneBot API 调用超时: {action}")
        if resp.get("status") == "failed":
            raise RuntimeError(
                f"OneBot API 失败: {action} retcode={resp.get('retcode')} "
                f"msg={resp.get('msg') or resp.get('wording')}"
            )
        return resp.get("data")

    # ---------- 消息发送 ----------

    async def send_group_msg(self, group_id: str | int, message: str) -> Any:
        return await self.call_api("send_group_msg", group_id=int(group_id), message=message)

    async def send_private_msg(self, user_id: str | int, message: str) -> Any:
        return await self.call_api("send_private_msg", user_id=int(user_id), message=message)

    async def send_group_forward_msg(self, group_id: str | int, messages: list[dict]) -> Any:
        return await self.call_api(
            "send_group_forward_msg", group_id=int(group_id), messages=messages
        )

    async def delete_msg(self, message_id: int) -> Any:
        return await self.call_api("delete_msg", message_id=int(message_id))

    async def send_like(self, user_id: str | int, times: int = 1) -> Any:
        return await self.call_api("send_like", user_id=int(user_id), times=times)

    # ---------- 群管理 ----------

    async def set_group_kick(self, group_id: str | int, user_id: str | int, reject_add_request: bool = False) -> Any:
        return await self.call_api(
            "set_group_kick",
            group_id=int(group_id),
            user_id=int(user_id),
            reject_add_request=reject_add_request,
        )

    async def set_group_ban(self, group_id: str | int, user_id: str | int, duration: int = 600) -> Any:
        return await self.call_api(
            "set_group_ban", group_id=int(group_id), user_id=int(user_id), duration=duration
        )

    async def set_group_admin(self, group_id: str | int, user_id: str | int, enable: bool = True) -> Any:
        return await self.call_api(
            "set_group_admin", group_id=int(group_id), user_id=int(user_id), enable=enable
        )

    async def set_group_whole_ban(self, group_id: str | int, enable: bool = True) -> Any:
        return await self.call_api("set_group_whole_ban", group_id=int(group_id), enable=enable)

    async def set_essence_msg(self, message_id: int) -> Any:
        return await self.call_api("set_essence_msg", message_id=int(message_id))

    async def send_group_notice(self, group_id: str | int, content: str) -> Any:
        return await self.call_api("send_group_notice", group_id=int(group_id), content=content)

    # ---------- 信息查询 ----------

    async def get_group_info(self, group_id: str | int) -> Any:
        return await self.call_api("get_group_info", group_id=int(group_id))

    async def get_group_list(self) -> Any:
        return await self.call_api("get_group_list")

    async def get_friend_list(self) -> Any:
        return await self.call_api("get_friend_list")

    async def get_stranger_info(self, user_id: str | int, no_cache: bool = True) -> Any:
        return await self.call_api(
            "get_stranger_info", user_id=int(user_id), no_cache=no_cache
        )

    async def get_group_member_info(self, group_id: str | int, user_id: str | int, no_cache: bool = True) -> Any:
        return await self.call_api(
            "get_group_member_info",
            group_id=int(group_id),
            user_id=int(user_id),
            no_cache=no_cache,
        )

    async def get_group_root_files(self, group_id: str | int) -> Any:
        return await self.call_api("get_group_root_files", group_id=int(group_id))

    async def get_group_files_by_folder(self, group_id: str | int, folder_id: str) -> Any:
        return await self.call_api(
            "get_group_files_by_folder", group_id=int(group_id), folder_id=folder_id
        )
