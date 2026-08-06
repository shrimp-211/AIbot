"""内容安全阶段:长度限制 + Prompt 注入模式拦截。"""
from __future__ import annotations

import re

from ...adapter.event import AgentEvent
from ...utils.config import Config
from ..scheduler import Stage

_INJECTION_PATTERNS = (
    r"(?i)ignore\s+(all\s+)?(previous|above|prior)\s+(instructions|prompts?|content)",
    r"(?i)disregard\s+(previous|above|prior)",
    r"(?i)you\s+are\s+now\s+",
    r"(?i)new\s+system\s+prompt",
    r"(?i)you\s+are\s+an?\s+unrestricted",
    r"忘记你(的|是|曾经)",
    r"忽略\s*(你|之前|以上).*指令",
    r"伪装\s*成",
    r"解除.*限制",
    r"越狱",
    r"DAN\s*模式",
)


class ContentSafetyStage(Stage):
    def __init__(self, config: Config):
        self._max_length = int(config.get("pipeline.content_safety.max_length", 5000) or 5000)
        self._patterns = _INJECTION_PATTERNS

    async def process(self, event: AgentEvent) -> None:
        text = event.plain_text
        if not text:
            return
        if len(text) > self._max_length:
            event.stop()
            await event.reply("消息过长,请分段发送。")
            return
        for pattern in self._patterns:
            if re.search(pattern, text):
                event.stop()
                await event.reply("抱歉,该消息包含不安全指令,已忽略。")
                return
