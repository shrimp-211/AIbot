"""配置加载:YAML + 环境变量覆盖 + 点号路径访问。

支持 `${ENV_VAR}` 和 `${ENV_VAR:-default}` 语法注入环境变量。
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml

_ENV_PATTERN = re.compile(r"\$\{([A-Z0-9_]+)(?::-([^}]*))?\}")


def _resolve_env(value: Any) -> Any:
    """递归解析字符串中的 ${ENV_VAR} / ${ENV_VAR:-default} 引用。"""
    if isinstance(value, str):

        def _repl(match: re.Match) -> str:
            name, default = match.group(1), match.group(2)
            return os.environ.get(name, default if default is not None else "")

        return _ENV_PATTERN.sub(_repl, value)
    if isinstance(value, dict):
        return {k: _resolve_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_env(v) for v in value]
    return value


class Config:
    """包装 dict 的配置对象,支持点号路径访问。"""

    def __init__(self, data: dict[str, Any]):
        self._data = data

    def get(self, dotted: str, default: Any = None) -> Any:
        """点号路径访问,如 `cfg.get('llm.provider.type')`。"""
        cur: Any = self._data
        for part in dotted.split("."):
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            else:
                return default
        return cur

    def __getitem__(self, dotted: str) -> Any:
        value = self.get(dotted)
        if value is None:
            raise KeyError(dotted)
        return value

    def raw(self) -> dict[str, Any]:
        return self._data


def load_config(path: str | Path) -> Config:
    """加载 YAML 配置文件并解析环境变量。"""
    path = Path(path)
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError(f"配置根节点必须是映射, got: {type(data)}")
    return Config(_resolve_env(data))
