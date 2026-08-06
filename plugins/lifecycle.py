"""插件生命周期钩子(参照 astrbot/nonebot 生命周期模型)。

插件可通过 @lifecycle.on("事件") 装饰器注册钩子,事件在引擎 HookManager 上触发:
- on_startup / on_shutdown: 启动/关闭
- on_agent_begin / on_agent_done: 单条消息处理开始/结束
- after_message_sent: 机器人发送回复后
- on_llm_request / on_llm_response: LLM 调用前后(可改写请求/响应)
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable

LIFECYCLE_EVENTS = (
    "on_startup",
    "on_shutdown",
    "on_agent_begin",
    "on_agent_done",
    "after_message_sent",
    "on_llm_request",
    "on_llm_response",
)


class PluginLifecycle:
    """插件生命周期注册器:把钩子注册到引擎 HookManager。"""

    def __init__(self, hook_manager: Any):
        self._hooks = hook_manager
        self._callbacks: dict[str, list[Callable[..., Awaitable[Any]]]] = {e: [] for e in LIFECYCLE_EVENTS}

    def on(self, event: str):
        """装饰器:@lifecycle.on('on_agent_done')"""
        if event not in LIFECYCLE_EVENTS:
            raise ValueError(f"未知生命周期事件: {event},可用: {LIFECYCLE_EVENTS}")

        def deco(func):
            self._callbacks[event].append(func)
            return func

        return deco

    def install(self) -> None:
        """把已注册的回调挂到 HookManager 对应事件上。"""
        if self._hooks is None:
            return
        for event, callbacks in self._callbacks.items():
            for cb in callbacks:
                self._hooks.register(_map_event(event), cb)

    def callbacks(self, event: str) -> list:
        return list(self._callbacks.get(event, []))


def _map_event(lifecycle_event: str) -> str:
    """插件生命周期事件 → HookManager 事件名。"""
    return {
        "on_startup": "session_start",
        "on_shutdown": "session_end",
        "on_agent_begin": "user_prompt_submit",
        "on_agent_done": "post_agent",
        "after_message_sent": "after_message_sent",
        "on_llm_request": "pre_llm_request",
        "on_llm_response": "post_llm_response",
    }.get(lifecycle_event, lifecycle_event)
