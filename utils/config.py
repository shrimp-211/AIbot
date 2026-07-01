"""配置加载器 — 支持 YAML 文件 + 环境变量覆盖."""

import os
import re
from pathlib import Path

import yaml


def _resolve_env(value: str) -> str:
    """解析字符串中的 ${ENV_VAR} 和 ${ENV_VAR:-default} 环境变量引用."""
    # 支持 ${VAR:-default}
    default_pattern = re.compile(r"\$\{(\w+):-([^}]+)\}")
    for match in default_pattern.finditer(value):
        var = match.group(1)
        default = match.group(2)
        env_val = os.environ.get(var, "")
        value = value.replace(match.group(0), env_val if env_val else default)

    # 普通 ${VAR}
    pattern = re.compile(r"\$\{(\w+)\}")
    for var in pattern.findall(value):
        env_val = os.environ.get(var, "")
        value = value.replace(f"${{{var}}}", env_val)
    return value


def _resolve_env_recursive(obj):
    """递归解析配置对象中的环境变量."""
    if isinstance(obj, str):
        return _resolve_env(obj)
    if isinstance(obj, dict):
        return {k: _resolve_env_recursive(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_resolve_env_recursive(v) for v in obj]
    return obj


class Config:
    """配置管理器 — 懒加载 YAML 配置，支持点号路径访问."""

    def __init__(self, path: str = "config.yaml"):
        self._path = Path(path)
        self._data: dict = {}

    def load(self) -> None:
        """加载并解析配置文件."""
        if not self._path.exists():
            raise FileNotFoundError(f"配置文件不存在: {self._path}")
        with open(self._path, "r", encoding="utf-8") as f:
            self._data = yaml.safe_load(f) or {}
        self._data = _resolve_env_recursive(self._data)

    def get(self, key: str, default=None):
        """支持点号分隔的嵌套键访问, 如 'onebot.port'."""
        keys = key.split(".")
        value = self._data
        for k in keys:
            if isinstance(value, dict):
                value = value.get(k)
            else:
                return default
            if value is None:
                return default
        return value

    def set(self, key: str, value) -> bool:
        """设置嵌套配置 (仅会话有效，不持久化)."""
        keys = key.split(".")
        current = self._data
        for k in keys[:-1]:
            if k not in current:
                current[k] = {}
            current = current[k]
        current[keys[-1]] = value
        return True

    def __getitem__(self, key: str):
        result = self.get(key)
        if result is None:
            raise KeyError(key)
        return result

    def __contains__(self, key: str) -> bool:
        return self.get(key) is not None

    @property
    def data(self) -> dict:
        return self._data


config = Config()


def load_config(path: str = "config.yaml") -> Config:
    """加载配置并返回全局配置实例."""
    config._path = Path(path)
    config.load()
    return config
