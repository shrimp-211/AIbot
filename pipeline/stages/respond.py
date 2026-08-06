"""响应阶段:逐段发送回复,段间间隔防刷屏。"""
from __future__ import annotations

import asyncio

from ...adapter.event import AgentEvent
from ..scheduler import Stage


class RespondStage(Stage):
    def __init__(self, interval: float = 0.3):
        self._interval = interval

    async def process(self, event: AgentEvent) -> None:
        segments = event.state.get("reply_segments") or []
        if not segments and event.state.get("reply"):
            segments = [str(event.state["reply"])]
        if not segments:
            return
        for i, seg in enumerate(segments):
            await event.reply(seg, at=(i == 0 and event.is_tome))
            if i < len(segments) - 1 and self._interval:
                await asyncio.sleep(self._interval)
