"""响应阶段:逐段发送回复,段间间隔防刷屏。"""
from __future__ import annotations

import asyncio

from ...adapter.event import AgentEvent
from ...adapter.message import escape_cq
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
        # Agent 最终回复是 LLM 生成的不可信文本,必须转义 CQ 码防注入
        # (插件/工具直连 event.reply 的路径由调用方自行转义,这里不动)
        for i, seg in enumerate(segments):
            await event.reply(escape_cq(seg), at=(i == 0 and event.is_tome))
            if i < len(segments) - 1 and self._interval:
                await asyncio.sleep(self._interval)
