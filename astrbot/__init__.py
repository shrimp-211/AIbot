"""AstrBot 兼容命名空间。

将 AstrBot 的 dashboard(WebUI)缝合进本项目:``astrbot.core`` 为适配层,
``astrbot.dashboard`` 为 AstrBot 原版 WebUI 后端代码。
"""

from astrbot.core.log import LogBroker, LogManager, logger

__all__ = ["logger", "LogBroker", "LogManager"]
