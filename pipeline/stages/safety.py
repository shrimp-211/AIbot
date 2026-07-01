"""第 3 阶段: 内容安全检查 — 参考 AstrBot ContentSafetyCheckStage."""

import re

from loguru import logger


class ContentSafetyStage:
    """内容安全检查."""

    _blocked_patterns = [
        re.compile(r"(?i)prompt\s*injection"),
        re.compile(r"(?i)ignore\s+(all\s+)?(previous|above)\s+instructions?"),
        re.compile(r"(?i)you\s+are\s+now\s+DAN"),
        re.compile(r"<\|im_start\|>"),
        re.compile(r"<\|im_end\|>"),
    ]

    def __init__(self, cfg):
        self._enabled = cfg.get("security.content_filter_enabled", True)
        self._max_input_len = 4000

    async def process(self, event):
        if not self._enabled:
            return

        text = event.text
        if len(text) > self._max_input_len:
            event.reply(f"消息太长 ({len(text)} 字), 请控制在 {self._max_input_len} 字以内")
            event.stop()
            return

        for pattern in self._blocked_patterns:
            if pattern.search(text):
                logger.warning(f"检测到注入模式: {pattern.pattern}")
                event.reply("消息包含不安全的模式，已被拦截")
                event.stop()
                return
