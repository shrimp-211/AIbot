"""限流阶段:固定窗口限流(默认 60s/30 条),带过期清理防内存泄漏。"""
from __future__ import annotations

import time

from ...adapter.event import AgentEvent
from ...utils.config import Config
from ..scheduler import Stage


class RateLimitStage(Stage):
    def __init__(self, config: Config):
        self._max_messages = int(config.get("pipeline.rate_limit.max_messages", 30) or 30)
        self._window = int(config.get("pipeline.rate_limit.window_seconds", 60) or 60)
        self._counts: dict[str, list[float]] = {}
        self._last_clean = time.time()

    async def process(self, event: AgentEvent) -> None:
        now = time.time()
        if now - self._last_clean > 300:
            self._cleanup(now)
        key = event.session_id
        stamps = [t for t in self._counts.get(key, []) if now - t < self._window]
        if len(stamps) >= self._max_messages:
            event.stop()
            await event.reply("消息频率过快,请稍后再试。")
            return
        stamps.append(now)
        self._counts[key] = stamps

    def _cleanup(self, now: float) -> None:
        expired = [k for k, v in self._counts.items() if not any(now - t < self._window for t in v)]
        for k in expired:
            del self._counts[k]
        self._last_clean = now
