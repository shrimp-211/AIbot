"""AstrBotConfig 兼容实现:桥接本项目的 YAML 配置(config.yaml)。

- 与 AstrBot 原版同为 ``dict`` 子类,提供 ``save_config``/``save_config_async`` 等接口
- 数据源为本项目 ``config.yaml``;通过 ``bind_project_config`` 绑定本项目 Config 实例
- dashboard 段(管理密码/jwt_secret 等)持久化到独立文件 ``data/dashboard.json``,
  **不污染 config.yaml**(避免重写丢失注释)
"""

from __future__ import annotations

import asyncio
import copy
import json
import os
import threading

from astrbot.core.utils.astrbot_path import get_astrbot_data_path, get_astrbot_path
from astrbot.core.utils.auth_password import (
    generate_dashboard_password,
    hash_dashboard_password,
    hash_md5_dashboard_password,
)

from .default import DEFAULT_CONFIG, DEFAULT_VALUE_MAP

_SINGLETON: "AstrBotConfig | None" = None


def _write_yaml(path: str, data: dict) -> None:
    import yaml

    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False, default_flow_style=False)
    os.replace(tmp, path)


def _read_yaml(path: str) -> dict:
    import yaml

    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


class AstrBotConfig(dict):
    """从本项目 ``config.yaml`` 加载的配置(dict 子类)。"""

    config_path: str
    default_config: dict
    schema: dict | None

    _project_config = None  # 本项目 Config 实例
    _config_file_path: str | None = None

    def __init__(
        self,
        config_path: str | None = None,
        default_config: dict = DEFAULT_CONFIG,
        schema: dict | None = None,
    ) -> None:
        super().__init__()
        global _SINGLETON
        object.__setattr__(
            self, "config_path",
            config_path or self._config_file_path
            or os.path.join(get_astrbot_path(), "config.yaml"),
        )
        object.__setattr__(self, "default_config", default_config)
        object.__setattr__(self, "schema", schema)
        object.__setattr__(self, "_save_lock", threading.Lock())
        if _SINGLETON is None:
            _SINGLETON = self
        self._load()

    # ---------------- 绑定本项目配置 ----------------

    @classmethod
    def bind_project_config(cls, config, config_file_path: str | None = None) -> None:
        """绑定主程序的 Config 实例(保存配置时同步主程序 + 写回 YAML)。"""
        cls._project_config = config
        cls._config_file_path = config_file_path or os.path.join(
            get_astrbot_path(), "config.yaml"
        )
        inst = _SINGLETON
        if inst is not None:
            inst.config_path = cls._config_file_path
            inst._load()

    @classmethod
    def get_singleton(cls) -> "AstrBotConfig | None":
        return _SINGLETON

    # ---------------- dashboard 段独立持久化 ----------------

    def _dashboard_secret_path(self) -> str:
        return os.path.join(get_astrbot_data_path(), "dashboard.json")

    def _read_dashboard_secret(self) -> dict | None:
        try:
            p = self._dashboard_secret_path()
            if os.path.exists(p):
                with open(p, encoding="utf-8") as f:
                    data = json.load(f) or {}
                return data if isinstance(data, dict) else None
        except Exception:
            pass
        return None

    def _write_dashboard_secret(self, data: dict) -> None:
        try:
            p = self._dashboard_secret_path()
            os.makedirs(os.path.dirname(p), exist_ok=True)
            tmp = p + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, p)
        except Exception:
            pass

    # ---------------- 加载 ----------------

    def _load(self) -> None:
        self.clear()
        if self._project_config is not None:
            self.update(copy.deepcopy(dict(self._project_config.raw())))
        else:
            self.update(_read_yaml(self.config_path))

        # dashboard 段:优先独立 secret 文件;其次 config.yaml 中已有段;否则初始化
        sec = self._read_dashboard_secret()
        if sec and isinstance(sec, dict) and sec.get("pbkdf2_password"):
            self["dashboard"] = sec
        elif isinstance(self.get("dashboard"), dict):
            self["dashboard"] = copy.deepcopy(self["dashboard"])
        else:
            self._init_dashboard_section()

    def _init_dashboard_section(self) -> None:
        import sys

        gen = None
        # 优先使用本项目配置的 webui.password 作为初始管理密码
        if self._project_config is not None:
            gen = self._project_config.get("webui.password") or None
        if not gen:
            gen = generate_dashboard_password()
            if self._project_config is None:
                # 未绑定主程序配置:仅内存占位,不写盘不打印(等待 bind_project_config 后初始化)
                self["dashboard"] = {
                    "username": "admin",
                    "pbkdf2_password": hash_dashboard_password(gen),
                    "password": hash_md5_dashboard_password(gen),
                    "password_storage_upgraded": True,
                    "password_change_required": True,
                    "jwt_secret": os.urandom(32).hex(),
                    "port": 8080,
                }
                return
            try:
                print(
                    f"\n[dashboard] 已生成 WebUI 管理密码: {gen}\n",
                    file=sys.stderr,
                    flush=True,
                )
            except Exception:
                pass
        dash = {
            "username": "admin",
            "pbkdf2_password": hash_dashboard_password(gen),
            "password": hash_md5_dashboard_password(gen),
            "password_storage_upgraded": True,
            "password_change_required": True,
            "jwt_secret": os.urandom(32).hex(),
            "port": 8080,
        }
        self["dashboard"] = dash
        # 持久化到独立文件,不污染 config.yaml
        self._write_dashboard_secret(dash)

    # ---------------- 保存 ----------------

    def save_config(self, replace_config: dict | None = None, *, indent: int = 2) -> bool:
        with self._save_lock:
            if replace_config:
                self.update(copy.deepcopy(replace_config))
            snapshot = copy.deepcopy(dict(self))
        self._persist(snapshot)
        return True

    async def save_config_async(
        self, replace_config: dict | None = None, *, indent: int = 2
    ) -> bool:
        return await asyncio.to_thread(self.save_config, replace_config, indent)

    def _persist(self, snapshot: dict) -> None:
        # dashboard 段不写入 config.yaml(避免重写丢失注释),独立持久化
        dash = snapshot.pop("dashboard", None)
        if dash is not None and isinstance(dash, dict):
            self._write_dashboard_secret(dash)
        if self._project_config is not None:
            raw = self._project_config.raw()
            raw.clear()
            raw.update(copy.deepcopy(snapshot))
            try:
                self._project_config.save()
            except Exception:
                _write_yaml(self.config_path, snapshot)
        else:
            _write_yaml(self.config_path, snapshot)

    # ---------------- 兼容方法 ----------------

    def check_config_integrity(self, refer_conf: dict, conf: dict, path: str = "") -> bool:
        return False

    @staticmethod
    def _config_schema_to_default_config(schema: dict) -> dict:
        out: dict = {}

        def _parse(s: dict, o: dict) -> None:
            for k, v in s.items():
                typ = v.get("type")
                if typ not in DEFAULT_VALUE_MAP:
                    raise TypeError(f"不受支持的配置类型 {typ}")
                if v.get("type") == "object":
                    o[k] = {}
                    _parse(v.get("items", {}), o[k])
                else:
                    o[k] = v.get("default", DEFAULT_VALUE_MAP[typ])

        _parse(schema, out)
        return out
