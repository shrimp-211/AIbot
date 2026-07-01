"""数据持久化 — JSON 文件存储.

简单的键值存储，用于持久化配置和状态。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


_db: dict[str, Any] = {}
_data_dir: str = "data"


def init_db(base_dir: str = "data") -> None:
    """初始化存储目录."""
    global _data_dir
    _data_dir = base_dir
    Path(_data_dir).mkdir(parents=True, exist_ok=True)


def _get_path(name: str) -> Path:
    return Path(_data_dir) / f"{name}.json"


def load(name: str) -> dict:
    """加载 JSON 数据."""
    path = _get_path(name)
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def save(name: str, data: dict) -> None:
    """保存 JSON 数据."""
    path = _get_path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
