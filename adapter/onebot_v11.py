"""OneBot v11 协议 WebSocket 适配器(服务端模式,支持多连接)。

通过 aiohttp 提供 WS 服务端,接收 NapCat / Lagrange / go-cqhttp 等
OneBot 客户端的连接,实现全双工通信:接收消息事件 + echo 关联的
异步 API 调用(`call_api` 带 30s 超时)。

支持多个 OneBot 客户端同时连入(如多 QQ 号),API 调用自动路由到
最近活跃的连接。
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from aiohttp import WSMsgType, web
from loguru import logger

from .base import BaseAdapter
from .driver import ReverseDriver, ReverseServerMixin
from .event import AgentEvent
from .message import MessageChain


class OneBotV11Adapter(ReverseServerMixin, BaseAdapter):
    platform = "qq"

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 6199,
        path: str = "/ws",
        token: str = "",
        self_id: str = "",
        driver: ReverseDriver | None = None,
    ):
        self.self_id = str(self_id)
        self.token = token
        self._init_server(host, port, path, driver)
        # 多连接管理:key = 客户端 self_id(缺省 "default")
        self._connections: dict[str, web.WebSocketResponse] = {}
        self._active_ws: web.WebSocketResponse | None = None
        self._echo_waiters: dict[str, asyncio.Future] = {}
        self._seq = 0
        self._lock = asyncio.Lock()
        # 最近消息缓存(message_id -> 快照),供群撤回拦截恢复原文
        self.message_cache: dict[int, dict[str, Any]] = {}
        self._msg_cache_max = 200
        # 机器人发送消息记录:会话键 -> (message_id, 时间戳),供 /撤回 使用
        self._bot_messages: dict[str, tuple[int, float]] = {}
        self._bot_msg_ttl = 3600

    def _register_driver_route(self, driver: ReverseDriver, path: str) -> None:
        driver.register_ws(path, self._ws_handler)

    def _register_routes(self, app: web.Application) -> None:
        app.router.add_get(self.path, self._ws_handler)

    async def start(self) -> None:
        await self._start_server("OneBot v11 WS", url_prefix="ws")

    async def stop(self) -> None:
        await self._stop_server()
        self._connections.clear()
        self._active_ws = None
        logger.info("OneBot v11 WS 适配器已停止")

    # ---------- WebSocket 连接 ----------

    async def _ws_handler(self, request: web.Request) -> web.WebSocketResponse:
        if self.token:
            auth = request.headers.get("Authorization", "")
            if auth != f"Bearer {self.token}":
                logger.warning("WebSocket 连接鉴权失败")
                return web.Response(status=401, text="Unauthorized")

        ws = web.WebSocketResponse(max_msg_size=16 * 1024 * 1024)
        await ws.prepare(request)
        key = "default"
        async with self._lock:
            self._connections[key] = ws
            self._active_ws = ws
        logger.info(f"OneBot 客户端已连接(连接数: {len(self._connections)})")

        try:
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    try:
                        data = json.loads(msg.data)
                    except json.JSONDecodeError:
                        logger.warning(f"无效 JSON: {msg.data[:200]}")
                        continue
                    self._on_frame(data, ws)
                elif msg.type == WSMsgType.ERROR:
                    break
        finally:
            async with self._lock:
                # 按 ws 对象清理所有指向它的 key,避免 key 名变化导致的泄漏
                stale = [k for k, v in self._connections.items() if v is ws]
                for k in stale:
                    self._connections.pop(k, None)
                if self._active_ws is ws:
                    self._active_ws = next(iter(self._connections.values()), None)
            logger.info(
                f"OneBot 客户端已断开(连接数: {len(self._connections)})"
            )
        return ws

    # ---------- 事件分发 ----------

    def _on_frame(self, data: dict[str, Any], ws: web.WebSocketResponse) -> None:
        if "echo" in data and "status" in data:
            echo = str(data.get("echo"))
            fut = self._echo_waiters.pop(echo, None)
            if fut is not None and not fut.done():
                fut.set_result(data)
            return

        post_type = data.get("post_type")
        if post_type == "message":
            sid = str(data.get("self_id", "") or "")
            if sid:
                asyncio.create_task(self._adopt_connection(sid, ws))
            self._active_ws = ws
            asyncio.create_task(self._handle_message(data))
        elif post_type in ("notice", "request"):
            # 通知/请求事件同样按 self_id 归位,交给管道处理
            sid = str(data.get("self_id", "") or "")
            if sid:
                asyncio.create_task(self._adopt_connection(sid, ws))
            if post_type == "notice":
                asyncio.create_task(self._handle_notice(data))
            else:
                asyncio.create_task(self._handle_request(data))

    async def _adopt_connection(self, sid: str, ws: web.WebSocketResponse) -> None:
        """按消息中的 self_id 将连接归位,支持多机器人同时在线。"""
        async with self._lock:
            if self._connections.get("default") is ws:
                self._connections.pop("default", None)
            self._connections[sid] = ws
            self._active_ws = ws

    async def _handle_message(self, data: dict[str, Any]) -> None:
        event = self._build_event(data)
        if event.message_id:
            self._cache_message(event)
        if self.on_event is not None:
            await self.on_event(event)

    async def _handle_notice(self, data: dict[str, Any]) -> None:
        if self.on_event is not None:
            await self.on_event(self._build_notice_event(data))

    async def _handle_request(self, data: dict[str, Any]) -> None:
        if self.on_event is not None:
            await self.on_event(self._build_request_event(data))

    def _cache_message(self, event: AgentEvent) -> None:
        """缓存最近消息,供群撤回拦截恢复原文(环形缓冲)。"""
        if event.message_id in self.message_cache:
            return
        self.message_cache[event.message_id] = {
            "text": event.raw_message or event.plain_text,
            "name": event.sender_name,
            "user_id": event.user_id,
            "group_id": event.group_id,
            "ts": time.time(),
        }
        while len(self.message_cache) > self._msg_cache_max:
            oldest = next(iter(self.message_cache), None)
            if oldest is None:
                break
            self.message_cache.pop(oldest, None)

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

    def _build_notice_event(self, data: dict[str, Any]) -> AgentEvent:
        """构造通知事件(group_increase/group_decrease/group_recall/...)。"""
        group_id = str(data.get("group_id", "")) if data.get("group_id") else None
        user_id = str(data.get("user_id", ""))
        return AgentEvent(
            platform="qq",
            event_type="notice",
            notice_type=data.get("notice_type", ""),
            sub_type=data.get("sub_type", ""),
            operator_id=str(data.get("operator_id", "") or ""),
            group_id=group_id,
            user_id=user_id,
            message_id=data.get("message_id"),
            session_id=group_id or user_id,
            _send_callback=self._reply,
        )

    def _build_request_event(self, data: dict[str, Any]) -> AgentEvent:
        """构造请求事件(friend 加好友 / group 加群)。flag 用于审批回执。"""
        group_id = str(data.get("group_id", "")) if data.get("group_id") else None
        user_id = str(data.get("user_id", ""))
        request_type = data.get("request_type", "")
        return AgentEvent(
            platform="qq",
            event_type="request",
            notice_type=request_type,  # friend | group
            sub_type=data.get("sub_type", ""),  # group: add | invite
            flag=str(data.get("flag", "") or ""),
            group_id=group_id,
            user_id=user_id,
            raw_message=data.get("comment", ""),
            session_id=group_id or user_id,
            _send_callback=self._reply,
        )

    # ---------- 发送 ----------

    async def _reply(self, event: AgentEvent, text: str, at: bool = False) -> None:
        if not text:
            return
        if at and event.message_type == "group":
            text = f"[CQ:at,qq={event.user_id}] {text}"
        if event.message_type == "group" and event.group_id:
            data = await self.send_group_msg(event.group_id, text)
        else:
            data = await self.send_private_msg(event.user_id, text)
        msg_id = data.get("message_id") if isinstance(data, dict) else None
        if msg_id:
            self._remember_bot_message(event.session_id, int(msg_id))

    def _remember_bot_message(self, conversation_key: str, message_id: int) -> None:
        now = time.time()
        stale = [k for k, (_, ts) in self._bot_messages.items() if now - ts > self._bot_msg_ttl]
        for k in stale:
            self._bot_messages.pop(k, None)
        self._bot_messages[conversation_key] = (message_id, now)

    async def recent_bot_message(self, conversation_key: str) -> int | None:
        item = self._bot_messages.get(conversation_key)
        if item is None:
            return None
        if time.time() - item[1] > self._bot_msg_ttl:
            self._bot_messages.pop(conversation_key, None)
            return None
        return item[0]

    async def forget_bot_message(self, conversation_key: str) -> None:
        self._bot_messages.pop(conversation_key, None)

    async def send_message(self, event: AgentEvent, text: str, at: bool = False) -> Any:
        return await self._reply(event, text, at=at)

    # ---------- 异步 API 调用 ----------

    async def call_api(self, action: str, **params: Any) -> Any:
        ws = self._active_ws
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

    # ---------- 请求审批 ----------

    async def set_friend_add_request(self, flag: str, approve: bool = True, remark: str = "") -> Any:
        return await self.call_api("set_friend_add_request", flag=flag, approve=approve, remark=remark)

    async def set_group_add_request(
        self, flag: str, sub_type: str = "add", approve: bool = True, reason: str = ""
    ) -> Any:
        return await self.call_api(
            "set_group_add_request", flag=flag, sub_type=sub_type, approve=approve, reason=reason
        )

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
