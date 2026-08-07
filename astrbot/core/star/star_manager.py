"""PluginManager 兼容实现:映射本项目插件注册表(PluginRegistry + PluginInstaller)。

插件实际管理能力来自本项目的 ``src/plugins/registry.PluginRegistry`` 与
``src/plugins/installer.PluginInstaller``。本类提供 AstrBot 接口。
"""

from __future__ import annotations

import os
from typing import Any

from astrbot.core import logger
from astrbot.core.utils.astrbot_path import get_astrbot_data_path


class PluginVersionUnsupportedError(Exception):
    pass


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
        return await self.installer.uninstall(plugin_name)

    async def uninstall_failed_plugin(self, plugin_name: str) -> bool:
        return False

    async def reload_failed_plugin(self, plugin_name: str) -> bool:
        return False

    async def inspect_plugin_repository(self, *args, **kwargs):
        return None

    def _validate_astrbot_version_specifier(self, *args, **kwargs):
        return True
