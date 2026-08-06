"""适配器抽象层:BaseAdapter + AdapterRegistry。

参考 NoneBot2 的 Driver/Adapter 设计,提供统一生命周期与事件分发,
让上层(管道/Cron/工具)不感知具体平台协议,支持多适配器并存。
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any, Awaitable, Callable

from .event import AgentEvent

logger = logging.getLogger("adapter")

EventCallback = Callable[[AgentEvent], Awaitable[None]]


class BaseAdapter(ABC):
    """平台适配器抽象基类。子类实现连接建立与消息收发。"""

    platform: str = "unknown"

    on_event: EventCallback | None = None

    @abstractmethod
    async def start(self) -> None:
        """建立连接 / 启动监听。"""

    @abstractmethod
    async def stop(self) -> None:
        """关闭连接 / 停止监听。"""

    @abstractmethod
    async def send_message(self, event: AgentEvent, text: str, at: bool = False) -> Any:
        """根据事件来源定位会话并发送消息。

        群聊发到 event.group_id,私聊发到 event.user_id;
        at=True 时在群聊中附带 @ 发送者(由子类处理 CQ 码/平台标记)。
        """

    async def call_api(self, action: str, **params: Any) -> Any:
        """调用平台原生 API。不支持时抛 NotImplementedError。"""
        raise NotImplementedError(f"{type(self).__name__} 不支持 API 调用: {action}")

    async def send_group_msg(self, group_id: str | int, message: str) -> Any:
        """便捷方法:向指定群发送消息(供 Cron 等无 event 场景使用)。"""
        return await self.call_api("send_group_msg", group_id=int(group_id), message=message)

    async def send_private_msg(self, user_id: str | int, message: str) -> Any:
        return await self.call_api("send_private_msg", user_id=int(user_id), message=message)


class AdapterRegistry:
    """多适配器注册中心:统一启动/停止,事件分发到全局回调。"""

    def __init__(self) -> None:
        self._adapters: dict[str, BaseAdapter] = {}
        self._on_event: EventCallback | None = None

    def register(self, name: str, adapter: BaseAdapter) -> BaseAdapter:
        adapter.on_event = self._dispatch
        self._adapters[name] = adapter
        logger.info("适配器已注册: %s (%s)", name, type(adapter).__name__)
        return adapter

    def unregister(self, name: str) -> None:
        adapter = self._adapters.pop(name, None)
        if adapter is not None:
            adapter.on_event = None

    def get(self, name: str) -> BaseAdapter | None:
        return self._adapters.get(name)

    def all(self) -> list[BaseAdapter]:
        return list(self._adapters.values())

    def names(self) -> list[str]:
        return list(self._adapters.keys())

    def set_callback(self, callback: EventCallback) -> None:
        """设置全局事件回调(收到任何适配器事件时调用)。"""
        self._on_event = callback

    async def _dispatch(self, event: AgentEvent) -> None:
        if self._on_event is not None:
            try:
                await self._on_event(event)
            except Exception:  # noqa: BLE001
                logger.exception("事件处理异常 platform=%s", event.platform)

    async def start_all(self) -> None:
        for name in self.names():
            await self._adapters[name].start()

    async def stop_all(self) -> None:
        for name in self.names():
            try:
                await self._adapters[name].stop()
            except Exception:  # noqa: BLE001
                logger.exception("适配器停止异常: %s", name)

    async def broadcast(self, event: AgentEvent, text: str, at: bool = False) -> None:
        """向所有适配器发送消息(跨平台广播)。"""
        for adapter in self.all():
            try:
                await adapter.send_message(event, text, at=at)
            except Exception:  # noqa: BLE001
                logger.exception("广播失败: %s", type(adapter).__name__)
