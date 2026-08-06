"""loguru 日志封装:控制台彩色输出 + 文件轮转。"""
from __future__ import annotations

import sys
from pathlib import Path

from loguru import logger


def setup_logger(
    name: str = "qqagent",
    log_dir: str | Path | None = None,
    level: str = "INFO",
) -> "logger":
    """配置全局 logger。调用多次会先清空已有 handler。"""
    logger.remove()

    console_fmt = (
        "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
        "<level>{level: <7}</level> | "
        "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
        "<level>{message}</level>"
    )
    logger.add(sys.stderr, level=level, format=console_fmt, enqueue=True)

    if log_dir:
        log_dir = Path(log_dir)
        log_dir.mkdir(parents=True, exist_ok=True)
        file_fmt = (
            "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <7} | "
            "{name}:{function}:{line} - {message}"
        )
        logger.add(
            log_dir / "agent_{time:YYYY-MM-DD}.log",
            level=level,
            format=file_fmt,
            rotation="10 MB",
            retention="30 days",
            encoding="utf-8",
            enqueue=True,
        )

    logger.opt(colors=True)
    logger.bind(name=name)
    return logger
