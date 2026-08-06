"""生命周期钩子系统(参考 Claude Code 事件模型,精简版)。

事件列表见 HOOK_EVENTS;处理器支持 matcher 过滤与前置/后置返回值。
"""
from __future__ import annotations

import fnmatch
from collections import defaultdict
from typing import Any, Awaitable, Callable

HOOK_EVENTS = (
    "user_prompt_submit",
    "pre_tool_use",
    "post_tool_use",
    "post_tool_use_failure",
    "session_start",
    "session_end",
    "stop",
    "stop_failure",
    "pre_compact",
    "post_compact",
    "permission_denied",
    "config_change",
)


class HookManager:
    def __init__(self):
        # event_name -> list[(matcher, handler)]
        self._handlers: dict[str, list[tuple[dict | None, Callable[..., Awaitable[Any]]]]] = (
            defaultdict(list)
        )

    def register(self, event: str, handler: Callable[..., Awaitable[Any]], matcher: dict | None = None) -> None:
        """注册钩子。matcher 形如 {"tool_name": "bash"} 或 {"key": "value"}。"""
        if event not in HOOK_EVENTS:
            raise ValueError(f"未知钩子事件: {event},可用: {', '.join(HOOK_EVENTS)}")
        self._handlers[event].append((matcher, handler))

    async def trigger(self, event: str, **kwargs: Any) -> Any:
        """触发钩子。对 pre_tool_use 类事件,处理器可返回 "block" 阻止执行。"""
        result = None
        for matcher, handler in self._handlers.get(event, []):
            if matcher and not self._match(matcher, kwargs):
                continue
            try:
                result = await handler(**kwargs)
            except Exception:  # noqa: BLE001
                continue
            if result == "block":
                return "block"
        return result

    @staticmethod
    def _match(matcher: dict, kwargs: dict) -> bool:
        for key, pattern in matcher.items():
            value = str(kwargs.get(key, ""))
            if not fnmatch.fnmatch(value.lower(), str(pattern).lower()):
                return False
        return True

    def list(self) -> dict[str, int]:
        return {event: len(handlers) for event, handlers in self._handlers.items()}
