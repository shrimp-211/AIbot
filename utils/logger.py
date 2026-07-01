"""日志系统 — 基于 loguru，支持彩色文本和 JSON 格式."""

import sys

from loguru import logger


def setup_logger(level: str = "INFO", fmt: str = "text", logfile: str = ""):
    """配置日志输出格式和目标."""
    logger.remove()

    if fmt == "json":
        logger.add(
            sys.stdout,
            level=level,
            serialize=True,
            enqueue=True,
        )
    else:
        logger.add(
            sys.stdout,
            level=level,
            format=(
                "<green>{time:HH:mm:ss}</green> | "
                "<level>{level: <8}</level> | "
                "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
                "<level>{message}</level>"
            ),
            colorize=True,
            enqueue=True,
        )

    if logfile:
        logger.add(
            logfile,
            level=level,
            rotation="10 MB",
            retention="7 days",
            enqueue=True,
            encoding="utf-8",
        )

    return logger
