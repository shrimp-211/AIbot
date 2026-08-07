"""AstrBot 兼容日志层。

- ``logger``: 包装本项目 loguru,兼容 AstrBot 的 ``%s`` 风格消息。
- ``LogManager``: AstrBot 日志管理器接口(no-op 兼容)。
- ``LogBroker``: 日志订阅器,供 dashboard 的日志 SSE API 使用。
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from typing import Any

from loguru import logger as _loguru


class _CompatLogger:
    """AstrBot logger 兼容包装。

    AstrBot 使用 ``%s`` 风格的 logging 消息;loguru 默认是 ``{}`` 风格。
    这里统一格式化:优先 ``%s``,其次 ``{}``,均失败则原样输出。
    """

    def _fmt(self, msg: Any, args: tuple) -> str:
        msg = str(msg)
        if not args:
            # 无参数:转义占位符,避免 loguru 因裸 {} 报格式化错误
            return msg.replace("{", "{{").replace("}", "}}")
        try:
            return msg % args  # logging 风格 %s / %d
        except Exception:
            try:
                return msg.format(*args)  # loguru 风格 {}
            except Exception:
                return msg

    def _emit(self, level: str, msg: Any, args: tuple, **kwargs: Any) -> None:
        exc_info = kwargs.pop("exc_info", None)
        text = self._fmt(msg, args)
        if level == "exception":
            _loguru.opt(exception=bool(exc_info) or True).error(text)
            return
        method = getattr(_loguru, level)
        method(text, **kwargs)

    def info(self, msg, *args, **kwargs):
        self._emit("info", msg, args, **kwargs)

    def warning(self, msg, *args, **kwargs):
        self._emit("warning", msg, args, **kwargs)

    def error(self, msg, *args, **kwargs):
        self._emit("error", msg, args, **kwargs)

    def debug(self, msg, *args, **kwargs):
        self._emit("debug", msg, args, **kwargs)

    def exception(self, msg, *args, **kwargs):
        self._emit("exception", msg, args, **kwargs)

    def success(self, msg, *args, **kwargs):
        self._emit("info", msg, args, **kwargs)


logger = _CompatLogger()


class LogManager:
    """AstrBot 日志管理器接口(本项目日志由 loguru 统一管理)。"""

    @staticmethod
    def GetLogger(log_name: str = "astrbot"):
        return logger

    @staticmethod
    def configure_logger(logger_obj, config) -> None:
        return None

    @staticmethod
    def configure_trace_logger(config) -> None:
        return None


class LogBroker:
    """日志订阅器:loguru sink 把每条日志推入缓存 + 订阅队列。"""

    def __init__(self, cache_size: int = 2000) -> None:
        self.log_cache: deque = deque(maxlen=cache_size)
        self._queues: set[asyncio.Queue] = set()
        self._sink_id: int | None = None
        self._install_sink()

    def _install_sink(self) -> None:
        if self._sink_id is None:
            self._sink_id = _loguru.add(self._sink, level="DEBUG")

    def _sink(self, message) -> None:
        try:
            record = message.record
            log_item = {
                "time": record["time"].timestamp(),
                "level": record["level"].name,
                "message": message,
                "logger": record["name"],
            }
        except Exception:
            log_item = {"time": time.time(), "level": "INFO", "message": str(message)}
        self.log_cache.append(log_item)
        for q in list(self._queues):
            try:
                q.put_nowait(log_item)
            except Exception:
                pass

    def register(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self._queues.add(q)
        return q

    def unregister(self, q: asyncio.Queue) -> None:
        self._queues.discard(q)
