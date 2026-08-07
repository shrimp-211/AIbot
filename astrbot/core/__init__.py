"""AstrBot 核心兼容层入口。

将 AstrBot 的 ``astrbot.core`` 接口映射到本项目服务,使 dashboard(WebUI)
代码以几乎原样的方式运行。本模块提供全局单例:
- ``logger`` / ``LogBroker`` / ``LogManager``:日志
- ``astrbot_config``:AstrBotConfig(dict 子类,桥接本项目 config.yaml)
- ``file_token_service``:临时文件令牌
- ``pip_installer``:pip 安装器
- ``sp``:SharedPreferences(桥接 dashboard 数据库)
- ``get_db_helper()``:dashboard 数据库(SQLModel + SQLite)
"""

from __future__ import annotations

import os

from astrbot.core.log import LogBroker, LogManager, logger
from astrbot.core.utils.astrbot_path import get_astrbot_data_path, get_astrbot_path

# 注意:file_token_service 等模块内部会 `from astrbot.core import logger`,
# 因此必须先导入 log(logger 定义)再导入它们。
from astrbot.core.file_token_service import FileTokenService
from astrbot.core.utils.pip_installer import PipInstaller

DEMO_MODE = os.getenv("DEMO_MODE", "").strip().lower() in ("true", "1", "t")


def _get_config():
    from astrbot.core.config.astrbot_config import AstrBotConfig

    inst = AstrBotConfig.get_singleton()
    if inst is None:
        inst = AstrBotConfig()
    return inst


astrbot_config = _get_config()
file_token_service = FileTokenService()

try:
    pip_installer = PipInstaller("", None)
except Exception:  # noqa: BLE001
    pip_installer = None

_db_helper = None


def get_db_helper():
    """返回 dashboard 数据库实例(SQLModel + SQLite,惰性创建)。"""
    global _db_helper
    if _db_helper is None:
        from astrbot.core.db.sqlite import SQLiteDatabase

        db_path = os.path.join(get_astrbot_data_path(), "dashboard.sqlite3")
        _db_helper = SQLiteDatabase(db_path)
    return _db_helper


def _get_sp():
    from astrbot.core.utils.shared_preferences import SharedPreferences

    return SharedPreferences(get_db_helper())


sp = _get_sp()

# 兼容 `from astrbot.core import db_helper`(如 workspace.py)
db_helper = get_db_helper()

__all__ = [
    "logger",
    "LogBroker",
    "LogManager",
    "DEMO_MODE",
    "astrbot_config",
    "file_token_service",
    "pip_installer",
    "sp",
    "db_helper",
    "get_db_helper",
    "get_astrbot_data_path",
    "get_astrbot_path",
]
