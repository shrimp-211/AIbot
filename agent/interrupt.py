"""中断机制:工具执行中可被请求中断(asyncio.Event 实现)。

按会话隔离;STOP/中断指令触发后,工具循环在每次工具调用前检查,
检测到中断即停止继续调用工具并返回已获得的结果。
"""
from __future__ import annotations

import asyncio


class InterruptController:
    """按会话的中断控制器。"""

    def __init__(self):
        self._events: dict[str, asyncio.Event] = {}

    def _event(self, session_id: str) -> asyncio.Event:
        ev = self._events.get(session_id)
        if ev is None:
            ev = asyncio.Event()
            self._events[session_id] = ev
        return ev

    def request(self, session_id: str) -> None:
        """请求中断某会话的工具执行。"""
        self._event(str(session_id)).set()

    def reset(self, session_id: str) -> None:
        """会话开始时清除中断标记。"""
        ev = self._events.get(str(session_id))
        if ev is not None:
            ev.clear()

    def is_interrupted(self, session_id: str) -> bool:
        ev = self._events.get(str(session_id))
        return ev.is_set() if ev is not None else False

    async def wait(self, session_id: str, timeout: float = 0.0) -> bool:
        """等待中断信号;返回是否被中断。timeout<=0 表示不等待仅查询。"""
        ev = self._event(str(session_id))
        if timeout <= 0:
            return ev.is_set()
        try:
            await asyncio.wait_for(ev.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            return False
