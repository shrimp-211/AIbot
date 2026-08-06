"""配置加载/校验/保存:YAML + 环境变量覆盖 + 点号路径访问。

- 支持 `${ENV_VAR}` 与 `${ENV_VAR:-default}` 语法注入环境变量
- `validate_config()` 依据内置 schema 做类型/范围校验,返回可读错误列表
- `save_config()` 原子写回(校验通过才落盘)
- `Config.reload()` 从文件热重载(WebUI 保存后调用)
"""
from __future__ import annotations

import os
import re
import tempfile
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


# ---------- 配置 schema(类型 / 范围) ----------

def _bool_schema() -> dict:
    return {"type": bool}


def _int_schema(lo: int | None = None, hi: int | None = None) -> dict:
    s: dict = {"type": int}
    if lo is not None:
        s["min"] = lo
    if hi is not None:
        s["max"] = hi
    return s


def _str_schema() -> dict:
    return {"type": str}


def _list_schema() -> dict:
    return {"type": list}


CONFIG_SCHEMA: dict = {
    "driver": {
        "type": dict,
        "children": {
            "enabled": _bool_schema(),
            "host": _str_schema(),
            "port": _int_schema(1, 65535),
        },
    },
    "onebot": {
        "type": dict,
        "children": {
            "host": _str_schema(),
            "port": _int_schema(1, 65535),
            "path": _str_schema(),
            "token": _str_schema(),
            "self_id": _str_schema(),
        },
    },
    "onebot_forward": {
        "type": dict,
        "children": {
            "enabled": _bool_schema(),
            "url": _str_schema(),
            "token": _str_schema(),
            "self_id": _str_schema(),
        },
    },
    "onebot_http": {
        "type": dict,
        "children": {
            "enabled": _bool_schema(),
            "host": _str_schema(),
            "port": _int_schema(1, 65535),
            "path": _str_schema(),
            "http_url": _str_schema(),
            "token": _str_schema(),
            "self_id": _str_schema(),
        },
    },
    "qq_official": {
        "type": dict,
        "children": {
            "enabled": _bool_schema(),
            "host": _str_schema(),
            "port": _int_schema(1, 65535),
            "path": _str_schema(),
            "app_id": _str_schema(),
            "app_secret": _str_schema(),
            "sign_secret": _str_schema(),
        },
    },
    "telegram": {
        "type": dict,
        "children": {
            "enabled": _bool_schema(),
            "token": _str_schema(),
            "allowed_chat_ids": _list_schema(),
            "poll_timeout": _int_schema(1, 50),
        },
    },
    "llm": {
        "type": dict,
        "children": {
            "provider": {
                "type": dict,
                "children": {
                    "type": _str_schema(),
                    "model": _str_schema(),
                    "api_key": _str_schema(),
                    "base_url": _str_schema(),
                    "max_tokens": _int_schema(1, 200000),
                    "temperature": {"type": (int, float), "min": 0, "max": 2},
                    "fallback_providers": _list_schema(),
                },
            }
        },
    },
    "search": {
        "type": dict,
        "children": {"tavily_key": _str_schema(), "brave_key": _str_schema()},
    },
    "mcp": {"type": dict, "children": {"servers": _list_schema()}},
    "agent": {
        "type": dict,
        "children": {
            "workdir": _str_schema(),
            "max_iterations": _int_schema(1, 64),
            # 0 = 按 provider 模型自动推断上下文窗口
            "max_context_tokens": _int_schema(0, 1000000),
        },
    },
    "cron": {
        "type": dict,
        "children": {
            "agent_enabled": _bool_schema(),
        },
    },
    "notice": {
        "type": dict,
        "children": {
            "welcome": {"type": dict, "children": {"enabled": _bool_schema(), "text": _str_schema()}},
            "farewell": {"type": dict, "children": {"enabled": _bool_schema(), "text": _str_schema()}},
            "anti_recall": {
                "type": dict,
                "children": {"enabled": _bool_schema(), "format": _str_schema()},
            },
            "friend_requests": _str_schema(),
            "group_requests": _str_schema(),
        },
    },
    "pipeline": {
        "type": dict,
        "children": {
            "group_whitelist": _list_schema(),
            "wake_words": _list_schema(),
            "command_prefixes": _list_schema(),
            "rate_limit": {
                "type": dict,
                "children": {
                    "max_messages": _int_schema(1),
                    "window_seconds": _int_schema(1),
                },
            },
            "content_safety": {
                "type": dict,
                "children": {"max_length": _int_schema(1)},
            },
        },
    },
    "security": {
        "type": dict,
        "children": {
            "admin_users": _list_schema(),
            "super_admin_users": _list_schema(),
            "trusted_folders": _list_schema(),
            "sandbox_enabled": _bool_schema(),
            "audit_enabled": _bool_schema(),
            "pairing_enabled": _bool_schema(),
        },
    },
    "webui": {
        "type": dict,
        "children": {
            "enabled": _bool_schema(),
            "host": _str_schema(),
            "port": _int_schema(1, 65535),
            "password": _str_schema(),
        },
    },
}


def validate_config(data: dict[str, Any]) -> list[str]:
    """依据 schema 校验配置,返回全部错误(空列表=通过)。"""
    errors: list[str] = []
    if not isinstance(data, dict):
        return ["配置根节点必须是映射"]

    def _check(section: str, value: Any, rule: dict) -> None:
        expected = rule.get("type")
        if expected is not None:
            ok = isinstance(value, expected)
            if value is None:
                # null 绕过校验会在运行时 int(None) 崩溃,一律视为非法
                errors.append(f"{section}: 不能为 null")
                return
            if isinstance(value, bool) and expected in (int, float):
                # bool 是 int 子类,显式拒绝 port:true 之类误写
                errors.append(f"{section}: 期望类型 {expected.__name__},实际 bool")
                return
            if not ok:
                errors.append(f"{section}: 期望类型 {expected.__name__},实际 {type(value).__name__}")
                return
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if "min" in rule and value < rule["min"]:
                errors.append(f"{section}: 不能小于 {rule['min']}(当前 {value})")
            if "max" in rule and value > rule["max"]:
                errors.append(f"{section}: 不能大于 {rule['max']}(当前 {value})")
        if isinstance(value, dict) and rule.get("children"):
            for child, child_rule in rule["children"].items():
                if child in value:
                    _check(f"{section}.{child}", value[child], child_rule)

    for section, rule in CONFIG_SCHEMA.items():
        if section in data:
            _check(section, data[section], rule)
    return errors


class Config:
    """包装 dict 的配置对象,支持点号路径访问。"""

    def __init__(self, data: dict[str, Any], path: str | Path | None = None):
        self._data = data
        self._path = Path(path) if path else None

    def get(self, dotted: str, default: Any = None) -> Any:
        """点号路径访问,如 `cfg.get('llm.provider.type')`。"""
        cur: Any = self._data
        for part in dotted.split("."):
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            else:
                return default
        return cur

    def set(self, dotted: str, value: Any) -> None:
        """设置点号路径(自动创建中间字典)。"""
        parts = dotted.split(".")
        cur = self._data
        for part in parts[:-1]:
            nxt = cur.get(part)
            if not isinstance(nxt, dict):
                nxt = {}
                cur[part] = nxt
            cur = nxt
        cur[parts[-1]] = value

    def __getitem__(self, dotted: str) -> Any:
        value = self.get(dotted)
        if value is None:
            raise KeyError(dotted)
        return value

    def raw(self) -> dict[str, Any]:
        return self._data

    def as_dict(self) -> dict[str, Any]:
        return dict(self._data)

    def path(self) -> Path | None:
        return self._path

    def reload(self) -> list[str]:
        """从磁盘重新加载配置(热更新)。返回校验错误(若有)。"""
        if self._path is None or not self._path.is_file():
            return ["无配置来源,无法重载"]
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
        except (OSError, yaml.YAMLError) as exc:
            return [f"配置解析失败: {exc}"]
        if not isinstance(data, dict):
            return ["配置根节点必须是映射"]
        self._data = _resolve_env(data)
        return validate_config(self._data)

    def save(self) -> list[str]:
        """原子写回当前配置到磁盘。返回校验错误(若有)。"""
        errors = validate_config(self._data)
        if errors or self._path is None:
            return errors
        return save_config(self._path, self._data)


def load_config(path: str | Path) -> Config:
    """加载 YAML 配置文件并解析环境变量,校验错误仅告警不阻断。"""
    path = Path(path)
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError(f"配置根节点必须是映射, got: {type(data)}")
    resolved = _resolve_env(data)
    errors = validate_config(resolved)
    if errors:
        import logging

        logging.getLogger("config").warning("配置校验告警(%d): %s", len(errors), " | ".join(errors[:5]))
    return Config(resolved, path=path)


def save_config(path: str | Path, data: dict[str, Any]) -> list[str]:
    """原子写回 YAML 配置(tmp 文件 + replace),返回校验错误(若有)。"""
    errors = validate_config(data)
    if errors:
        return errors
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
    return []
