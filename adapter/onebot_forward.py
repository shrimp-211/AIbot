"""OneBot v11 正向 WebSocket 客户端适配器。

作为客户端连接 OneBot 实现端(NapCat / Lagrange / go-cqhttp)提供的
WS 服务端地址,支持断线自动重连与心跳检测。

适用场景:机器人被部署在内网/容器内,无法被 OneBot 端主动连入时,
使用反向连接的镜像模式。
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import aiohttp
from loguru import logger

from .base import BaseAdapter
from .driver import TaskTrackerMixin
from .event import AgentEvent
from .onebot_events import build_message_event, build_notice_event, build_request_event


class OneBotV11Client(TaskTrackerMixin, BaseAdapter):
    platform = "qq"

    def __init__(
        self,
        url: str,
        token: str = "",
        self_id: str = "",
        reconnect_interval: float = 5.0,
        heartbeat_interval: float = 30.0,
    ):
        self.url = url
        self.token = token
        self.self_id = self_id
        self._reconnect_interval = reconnect_interval
        self._heartbeat_interval = heartbeat_interval

        self._session: aiohttp.ClientSession | None = None
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._running = False
        self._tasks: set[asyncio.Task] = set()
        self._loop_task: asyncio.Task | None = None
        self._echo_waiters: dict[str, asyncio.Future] = {}
        self._seq = 0
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._session = aiohttp.ClientSession()
        self._loop_task = asyncio.get_running_loop().create_task(self._connect_loop())
        logger.info(f"OneBot v11 正向 WS 客户端启动: {self.url}")

    async def stop(self) -> None:
        self._running = False
        if self._loop_task is not None:
            self._loop_task.cancel()
            try:
                await self._loop_task
            except asyncio.CancelledError:
                pass
            self._loop_task = None
        if self._ws is not None:
            await self._ws.close()
            self._ws = None
        if self._session is not None:
            await self._session.close()
            self._session = None
        # 停止时立即失败在途 API 调用,避免悬挂至 30s 超时
        self._fail_pending_waiters("适配器停止")
        await self._drain_tasks()
        logger.info("OneBot v11 正向 WS 客户端已停止")

    def _fail_pending_waiters(self, reason: str) -> None:
        """在途 API 调用在连接断开/停止时立即失败,避免悬挂至 30s 超时。"""
        pending = list(self._echo_waiters.items())
        for echo, fut in pending:
            if not fut.done():
                fut.set_exception(ConnectionError(f"OneBot 正向 WS {reason}"))
        self._echo_waiters.clear()

    async def _connect_loop(self) -> None:
        while self._running:
            try:
                await self._connect_once()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                logger.warning("正向 WS 连接失败,{:.0f}s 后重试: {}", self._reconnect_interval, self.url)
            if self._running:
                await asyncio.sleep(self._reconnect_interval)

    async def _connect_once(self) -> None:
        session = self._session
        if session is None:
            return
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        ws = await session.ws_connect(
            self.url,
            headers=headers,
            heartbeat=self._heartbeat_interval,
            max_msg_size=16 * 1024 * 1024,
        )
        self._ws = ws
        logger.info(f"正向 WS 已连接: {self.url}")

        try:
            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    try:
                        data = json.loads(msg.data)
                    except json.JSONDecodeError:
                        logger.warning(f"无效 JSON: {msg.data[:200]}")
                        continue
                    self._on_frame(data)
                elif msg.type == aiohttp.WSMsgType.ERROR:
                    break
        finally:
            if self._ws is ws:
                self._ws = None
            # 连接断开:在途 echo 请求立即失败,避免悬挂
            self._fail_pending_waiters("连接断开")
            logger.info("正向 WS 连接断开")

    # ---------- 事件分发 ----------

    def _on_frame(self, data: dict[str, Any]) -> None:
        if "echo" in data and "status" in data:
            echo = str(data.get("echo"))
            fut = self._echo_waiters.pop(echo, None)
            if fut is not None and not fut.done():
                fut.set_result(data)
            return

        if self.on_event is None:
            return
        post_type = data.get("post_type")
        if post_type == "message":
            self._spawn(self.on_event(self._build_event(data)))
        elif post_type == "notice":
            self._spawn(self.on_event(self._build_notice_event(data)))
        elif post_type == "request":
            self._spawn(self.on_event(self._build_request_event(data)))

    def _build_event(self, data: dict[str, Any]) -> AgentEvent:
        return build_message_event("qq", self.self_id, data, self._reply)

    def _build_notice_event(self, data: dict[str, Any]) -> AgentEvent:
        return build_notice_event("qq", data, self._reply)

    def _build_request_event(self, data: dict[str, Any]) -> AgentEvent:
        return build_request_event("qq", data, self._reply)

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

    async def call_api(self, action: str, **params: Any) -> Any:
        ws = self._ws
        if ws is None or ws.closed:
            raise ConnectionError("OneBot 正向 WS 未连接")
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
