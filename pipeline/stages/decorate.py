"""装饰阶段:超长回复按自然段/字符分段,防止单条消息超限。"""
from __future__ import annotations

from ...adapter.event import AgentEvent
from ..scheduler import Stage


class DecorateStage(Stage):
    def __init__(self, max_chars: int = 4000):
        self._max_chars = max_chars

    async def process(self, event: AgentEvent) -> None:
        reply = event.state.get("reply")
        if not reply:
            return
        event.state["reply_segments"] = self._split(str(reply), self._max_chars)

    @staticmethod
    def _split(text: str, max_chars: int) -> list[str]:
        if len(text) <= max_chars:
            return [text]
        paragraphs = text.split("\n\n")
        segments: list[str] = []
        current = ""
        for p in paragraphs:
            if len(current) + len(p) + 2 > max_chars:
                if current:
                    segments.append(current.strip())
                while len(p) > max_chars:
                    segments.append(p[:max_chars])
                    p = p[max_chars:]
                current = p
            else:
                current = (current + "\n\n" + p) if current else p
        if current.strip():
            segments.append(current.strip())
        return segments or [text]
