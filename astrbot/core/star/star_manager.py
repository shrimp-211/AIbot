"""PluginManager 兼容实现:映射本项目插件注册表(PluginRegistry + PluginInstaller)。

插件实际管理能力来自本项目的 ``src/plugins/registry.PluginRegistry`` 与
``src/plugins/installer.PluginInstaller``。本类提供 AstrBot 接口。
"""

from __future__ import annotations

import os
from typing import Any

from astrbot.core import logger
from astrbot.core.star.star import StarMetadata
from astrbot.core.utils.astrbot_path import get_astrbot_data_path


class PluginVersionUnsupportedError(Exception):
    pass


def build_star_from_meta(name: str, meta: dict) -> StarMetadata:
    """把本项目插件的 metadata 转成 AstrBot star(补齐 dashboard 序列化所需字段)。"""
    star = StarMetadata(
        name=name,
        description=str(meta.get("description") or meta.get("desc") or ""),
        version=str(meta.get("version") or "1.0.0"),
        author=str(meta.get("author") or ""),
    )
    star.desc = star.description
    star.display_name = str(meta.get("display_name") or meta.get("name") or name)
    star.repo = meta.get("repo") or meta.get("repository")
    star.reserved = bool(meta.get("reserved", False))
    star.activated = True
    star.support_platforms = []
    star.astrbot_version = str(meta.get("astrbot_version") or "")
    star.i18n = {}
    star.root_dir_name = name
    star.logo_path = meta.get("logo_path") or ""
    star.star_handler_full_names = []
    star.skills = []
    return star


class StarContext:
    """插件上下文(star 实例集合 + 插件注册的 Web API)。"""

    def __init__(self) -> None:
        self._stars: list = []
        self.registered_web_apis: dict[str, Any] = {}

    def register_star(self, star: Any) -> None:
        self._stars.append(star)

    def get_all_stars(self) -> list:
        return list(self._stars)

    def get_star(self, plugin_name: str):
        for star in self._stars:
            if getattr(star, "name", None) == plugin_name:
                return star
        return None


class PluginManager:
    def __init__(
        self,
        plugin_registry=None,
        installer=None,
        plugin_dirs: list[str] | None = None,
    ) -> None:
        self.registry = plugin_registry  # 本项目 PluginRegistry
        self.installer = installer  # 本项目 PluginInstaller
        self.context = StarContext()
        self._plugin_dirs = plugin_dirs or [os.path.join(get_astrbot_data_path(), "plugins")]
        self._failed_plugins: dict = {}

    # ---- 路径 ----
    @property
    def plugin_store_path(self) -> str:
        return os.path.join(get_astrbot_data_path(), "plugins")

    @property
    def reserved_plugin_path(self) -> str:
        return os.path.join(get_astrbot_data_path(), "plugins")

    @property
    def failed_plugin_dict(self) -> dict:
        return self._failed_plugins

    # ---- 本项目插件能力映射 ----
    async def load_and_sync_stars(self) -> None:
        """加载本项目插件目录到 registry,并把插件元数据同步为 dashboard star。"""
        if self.registry is None:
            return
        for d in self._plugin_dirs:
            try:
                await self.registry.load_from_directory(d)
            except Exception as exc:  # noqa: BLE001
                logger.error(f"插件目录加载失败: {d}: {exc}")
        self._sync_stars_from_registry()

    def _sync_stars_from_registry(self) -> None:
        """把 registry 中已加载插件的元数据注册为 dashboard star。"""
        if self.registry is None or not hasattr(self.registry, "plugin_metadata"):
            return
        try:
            metas = self.registry.plugin_metadata()
        except Exception:  # noqa: BLE001
            return
        existing = {s.name for s in self.context.get_all_stars()}
        for name, meta in metas.items():
            if name in existing:
                continue
            self.context.register_star(build_star_from_meta(name, meta))

    async def reload(self, plugin_name: str | None = None) -> bool:
        try:
            if self.registry is not None:
                for d in self._plugin_dirs:
                    await self.registry.reload_from_directory(d)
            return True
        except Exception as exc:  # noqa: BLE001
            logger.error(f"插件重载失败: {exc}")
            return False

    async def turn_on_plugin(self, plugin_name: str) -> bool:
        if self.registry is None:
            return False
        self.registry.set_enabled(plugin_name, True)
        return True

    async def turn_off_plugin(self, plugin_name: str) -> bool:
        if self.registry is None:
            return False
        self.registry.set_enabled(plugin_name, False)
        return True

    async def install_plugin(self, *args, **kwargs):
        if self.installer is None:
            raise RuntimeError("未配置插件安装器")
        return await self.installer.install(*args, **kwargs)

    async def install_plugin_from_file(self, *args, **kwargs):
        return await self.install_plugin(*args, **kwargs)

    async def update_plugin(self, *args, **kwargs):
        return None

    async def uninstall_plugin(self, plugin_name: str, **kwargs) -> bool:
        if self.installer is None:
            return False
        result = self.installer.uninstall(plugin_name)
        if isinstance(result, dict):
            return bool(result.get("ok", False))
        return bool(result)

    async def uninstall_failed_plugin(self, plugin_name: str) -> bool:
        return False

    async def reload_failed_plugin(self, plugin_name: str) -> bool:
        return False

    async def inspect_plugin_repository(self, *args, **kwargs):
        return None

    def _validate_astrbot_version_specifier(self, *args, **kwargs):
        return True
