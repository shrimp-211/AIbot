"""第 2 阶段: 频率限制 — 防止滥用."""

import time
from collections import defaultdict

from loguru import logger


class RateLimitStage:
    """固定窗口频率限制器.

    默认: 60s 内最多 30 条消息。
    """

    def __init__(self, cfg):
        window = cfg.get("agent.rate_limit.window_sec", 60)
        max_req = cfg.get("agent.rate_limit.max_requests", 30)
        self._window = window
        self._max = max_req
        self._buckets: dict[str, list[float]] = defaultdict(list)
        self._last_cleanup = time.time()
        self._cleanup_interval = window * 2  # 每 2 个窗口清理一次

    async def process(self, event):
        """检查频率限制."""
        key = event.get_session_id()
        now = time.time()

        # 定期清理过期 key 防止内存泄漏
        if now - self._last_cleanup > self._cleanup_interval:
            stale_keys = [
                k for k, v in self._buckets.items()
                if not v or all(now - t >= self._window for t in v)
            ]
            for k in stale_keys:
                del self._buckets[k]
            self._last_cleanup = now

        # 清理过期记录
        self._buckets[key] = [
            t for t in self._buckets[key]
            if now - t < self._window
        ]

        if len(self._buckets[key]) >= self._max:
            logger.warning(f"频率限制触发: {key}")
            event.reply("消息太频繁了，请稍后再试。")
            event.stop()
            return

        self._buckets[key].append(now)
