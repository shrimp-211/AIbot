"""AstrBot 兼容路径助手:映射到本项目的目录结构。

- ``get_astrbot_path()`` 返回 Python 包根(内含 config.yaml)
- ``get_astrbot_root()`` 返回项目根
- 数据目录为 ``<root>/data``
"""

from __future__ import annotations

import os
import tempfile

from astrbot.core.utils.runtime_env import is_packaged_desktop_runtime

_SRC_ROOT = os.path.realpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../../"))
# QQbot/src/astrbot/core/utils/astrbot_path.py 的上级第 4 层即项目根 QQbot
_PROJECT_ROOT = os.path.realpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../../../"))


def get_astrbot_path() -> str:
    """项目源码根(即 Python 包根 ``src``)。"""
    return _SRC_ROOT


def get_astrbot_root() -> str:
    """项目根目录(``data`` 所在目录)。"""
    if path := os.environ.get("ASTRBOT_ROOT"):
        return os.path.realpath(path)
    if is_packaged_desktop_runtime():
        return os.path.realpath(os.path.join(os.path.expanduser("~"), ".astrbot"))
    return _PROJECT_ROOT


def get_astrbot_data_path() -> str:
    return os.path.realpath(os.path.join(get_astrbot_root(), "data"))


def get_astrbot_config_path() -> str:
    return os.path.realpath(os.path.join(get_astrbot_data_path(), "config"))


def get_astrbot_plugin_path() -> str:
    return os.path.realpath(os.path.join(get_astrbot_data_path(), "plugins"))


def get_astrbot_builtin_plugin_path() -> str:
    return os.path.realpath(os.path.join(get_astrbot_path(), "astrbot", "builtin_stars"))


def get_astrbot_plugin_data_path() -> str:
    return os.path.realpath(os.path.join(get_astrbot_data_path(), "plugin_data"))


def get_astrbot_t2i_templates_path() -> str:
    return os.path.realpath(os.path.join(get_astrbot_data_path(), "t2i_templates"))


def get_astrbot_webchat_path() -> str:
    return os.path.realpath(os.path.join(get_astrbot_data_path(), "webchat"))


def get_astrbot_temp_path() -> str:
    return os.path.realpath(os.path.join(get_astrbot_data_path(), "temp"))


def get_astrbot_skills_path() -> str:
    return os.path.realpath(os.path.join(get_astrbot_data_path(), "skills"))


def get_astrbot_workspaces_path() -> str:
    return os.path.realpath(os.path.join(get_astrbot_data_path(), "workspaces"))


def get_astrbot_system_tmp_path() -> str:
    return os.path.realpath(os.path.join(tempfile.gettempdir(), ".astrbot"))


def get_astrbot_site_packages_path() -> str:
    return os.path.realpath(os.path.join(get_astrbot_data_path(), "site-packages"))


def get_astrbot_knowledge_base_path() -> str:
    return os.path.realpath(os.path.join(get_astrbot_data_path(), "knowledge_base"))


def get_astrbot_backups_path() -> str:
    return os.path.realpath(os.path.join(get_astrbot_data_path(), "backups"))
