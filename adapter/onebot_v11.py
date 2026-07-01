"""OneBot v11 协议适配器 — 全双工 WebSocket 通信.

参考 NoneBot2 的 Adapter 设计模式，将 OneBot v11 协议消息
转换为统一的 AgentEvent 模型。

使用 aiohttp 实现 WebSocket 服务端，接收 OneBot 客户端连接。
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable, Coroutine
from typing import Any

from aiohttp import web

from .event import AgentEvent, EventType, MessageType
from .message import MessageChain

_API_TIMEOUT = 30  # API 响应超时 (秒)


class OneBotV11Adapter:
    """OneBot v11 适配器 — WebSocket 服务端."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 6199,
        path: str = "/ws",
        access_token: str = "",
    ):
        self.host = host
        self.port = port
        self.path = path
        self.access_token = access_token

        self._app: web.Application | None = None
        self._runner: web.AppRunner | None = None
        self._ws: web.WebSocketResponse | None = None
        self._running = False
        self._pending_calls: dict[str, asyncio.Future] = {}
        """echo → Future, 用于 API 响应关联"""

        self.on_message: Callable[[AgentEvent], Coroutine] | None = None
        self._logger = None

    @property
    def logger(self):
        if self._logger is None:
            from loguru import logger as _logger
            self._logger = _logger
        return self._logger

    # ---- 消息解析 ----

    def _parse_event(self, raw: dict[str, Any]) -> AgentEvent | None:
        """将 OneBot v11 事件 JSON 解析为 AgentEvent."""
        post_type = raw.get("post_type", "")
        if post_type != "message":
            return None

        message_type_str = raw.get("message_type", "group")
        message_type = MessageType.GROUP if message_type_str == "group" else MessageType.PRIVATE

        user_id = str(raw.get("user_id", ""))
        group_id = str(raw.get("group_id", "")) if message_type == MessageType.GROUP else ""

        sender = raw.get("sender", {})
        user_name = sender.get("nickname", sender.get("card", user_id))

        raw_message = raw.get("message", "")
        message_id = str(raw.get("message_id", ""))

        # 解析 CQ 码消息为 MessageChain
        message_chain = MessageChain.from_cq_string(raw_message)
        plain_text = message_chain.extract_text()

        # 判断是否 @了机器人
        is_tome = False
        self_id = str(raw.get("self_id", ""))
        for seg in message_chain.filter("at"):
            if seg.data.get("qq") == self_id:
                is_tome = True
                break

        event = AgentEvent(
            type=EventType.MESSAGE,
            message_type=message_type,
            user_id=user_id,
            user_name=user_name,
            group_id=group_id,
            message_id=message_id,
            message=plain_text,
            message_chain=message_chain,
            raw_event=raw,
            is_tome=is_tome,
            session_id=f"group_{group_id}_{user_id}" if message_type == MessageType.GROUP else f"private_{user_id}",
        )

        return event

    # ---- WebSocket 处理 ----

    async def _handler(self, request: web.Request) -> web.WebSocketResponse:
        """WebSocket 连接处理入口."""
        # Token 鉴权 (必须设置 access_token 或使用 IP 白名单)
        if self.access_token:
            auth_header = request.headers.get("Authorization", "")
            if auth_header != f"Bearer {self.access_token}":
                self.logger.warning("WebSocket 鉴权失败: token不匹配")
                return web.Response(status=401)
        else:
            # 无 token 时仅允许本地连接
            peer = request.transport.get_extra_info("peername")
            if peer and peer[0] not in ("127.0.0.1", "::1", "localhost"):
                self.logger.warning(f"非本地连接被拒绝: {peer}")
                return web.Response(status=403)

        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self._ws = ws
        self.logger.info(f"OneBot 客户端已连接: {request.remote}")

        async for msg in ws:
            if msg.type == web.WSMsgType.TEXT:
                try:
                    raw = json.loads(msg.data)
                except json.JSONDecodeError:
                    self.logger.warning(f"无效 JSON: {msg.data[:100]}")
                    continue

                # 处理心跳
                if raw.get("meta_event_type") == "heartbeat":
                    if not ws.closed:
                        await ws.send_json({
                            "action": ".handle_heartbeat",
                            "params": {"status": {}},
                            "echo": raw.get("echo", ""),
                        })
                    continue

                # API 响应: 有 "status" 字段且无 "post_type"
                if "status" in raw and "post_type" not in raw:
                    echo = raw.get("echo", "")
                    if echo and echo in self._pending_calls:
                        future = self._pending_calls.pop(echo)
                        if not future.done():
                            future.set_result(raw)
                    continue

                # 消息事件
                event = self._parse_event(raw)
                if event and self.on_message:
                    try:
                        await self.on_message(event)
                    except Exception:
                        self.logger.exception("消息处理异常")

            elif msg.type == web.WSMsgType.ERROR:
                self.logger.error(f"WebSocket 错误: {ws.exception()}")

            elif msg.type == web.WSMsgType.CLOSE:
                self.logger.info("OneBot 客户端断开连接")
                break

        return ws

    # ---- OneBot API 通用调用 ----

    async def call_api(self, action: str, params: dict = None) -> dict | None:
        """调用任意 OneBot v11 API 并等待响应.

        Args:
            action: API 动作名 (如 'get_group_info', 'set_group_kick')
            params: 参数字典

        Returns:
            API 响应 data，失败返回 None
        """
        if not self._ws or self._ws.closed:
            self.logger.warning(f"WS未连接，无法调用API: {action}")
            return None

        import uuid
        echo = str(uuid.uuid4())[:8]
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending_calls[echo] = future

        payload = {
            "action": action,
            "params": params or {},
            "echo": echo,
        }

        try:
            await self._ws.send_json(payload)
            self.logger.debug(f"API调用: {action}({params})")

            # 等待响应 (带超时)
            raw = await asyncio.wait_for(future, timeout=_API_TIMEOUT)

            if raw.get("retcode", 0) != 0:
                self.logger.warning(f"API失败: {action} → retcode={raw.get('retcode')}, msg={raw.get('msg','')}")
                return {"status": "failed", "retcode": raw.get("retcode"), "msg": raw.get("msg", "")}

            return {"status": "ok", "data": raw.get("data"), "echo": echo}

        except asyncio.TimeoutError:
            self._pending_calls.pop(echo, None)
            self.logger.error(f"API超时 ({_API_TIMEOUT}s): {action}")
            return {"status": "timeout", "echo": echo}
        except Exception:
            self._pending_calls.pop(echo, None)
            self.logger.exception(f"API调用失败: {action}")
            return None

    async def send_raw(self, message: str, user_id: str = "", group_id: str = "") -> bool:
        """直接发送消息 (不依赖 AgentEvent).

        Args:
            message: 消息内容 (CQ码格式)
            user_id: 目标用户QQ号
            group_id: 目标群号

        Returns:
            是否发送成功
        """
        if not self._ws or self._ws.closed:
            return False

        try:
            if group_id:
                payload = {
                    "action": "send_group_msg",
                    "params": {"group_id": int(group_id), "message": message},
                }
            elif user_id:
                payload = {
                    "action": "send_private_msg",
                    "params": {"user_id": int(user_id), "message": message},
                }
            else:
                return False

            await self._ws.send_json(payload)
            return True
        except Exception:
            self.logger.exception("send_raw 失败")
            return False

    # ---- 标准消息发送 ----

    async def send(self, event: AgentEvent, message: str | MessageChain) -> bool:
        """发送消息到 QQ.

        Args:
            event: 原始事件 (用于确定回复目标)
            message: 回复内容 (纯文本或 MessageChain)

        Returns:
            是否发送成功
        """
        if not self._ws or self._ws.closed:
            self.logger.warning("WebSocket 未连接，无法发送消息")
            return False

        if isinstance(message, str):
            cq_string = message
        else:
            cq_string = message.to_cq_string()

        # 构建 OneBot v11 send_msg API 调用
        if event.is_group:
            api_payload = {
                "action": "send_group_msg",
                "params": {
                    "group_id": int(event.group_id),
                    "message": cq_string,
                },
            }
        else:
            api_payload = {
                "action": "send_private_msg",
                "params": {
                    "user_id": int(event.user_id),
                    "message": cq_string,
                },
            }

        try:
            await self._ws.send_json(api_payload)
            return True
        except Exception:
            self.logger.exception("发送消息失败")
            return False

    # ---- 生命周期 ----

    async def run(self, stop_event: asyncio.Event) -> None:
        """启动 WebSocket 服务器."""
        self._app = web.Application()
        self._app.router.add_get(self.path, self._handler)

        self._runner = web.AppRunner(self._app)
        await self._runner.setup()

        site = web.TCPSite(self._runner, self.host, self.port)
        await site.start()

        self._running = True
        self.logger.info(f"OneBot 适配器已启动: ws://{self.host}:{self.port}{self.path}")

        await stop_event.wait()

    async def stop(self) -> None:
        """关闭适配器."""
        self._running = False
        if self._ws and not self._ws.closed:
            await self._ws.close()
        if self._runner:
            await self._runner.cleanup()
        self.logger.info("OneBot 适配器已关闭")
