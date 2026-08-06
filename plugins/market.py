"""插件市场(参照 AstrBot 插件市场):从在线注册表获取插件列表,支持一键安装。

- 注册表:远端 JSON {plugins: [{name, description, source(git/dir), version, author, tags}]}
- 网络失败/未配置时回退到内置精选列表(保证页面始终可用)
- 安装复用 PluginInstaller(git clone + 依赖 + 元数据校验)
"""
from __future__ import annotations

from typing import Any

import httpx
from loguru import logger

# 内置精选插件(网络不可用时兜底展示)
_BUILTIN_PLUGINS = [
    {
        "name": "word_cloud",
        "description": "生成群聊词云图(统计高频词)",
        "source": "https://github.com/example/qqbot-plugin-wordcloud.git",
        "version": "1.0.0",
        "author": "内置示例",
        "tags": ["娱乐"],
    },
    {
        "name": "daily_report",
        "description": "每日早报:聚合新闻要点定时推送",
        "source": "https://github.com/example/qqbot-plugin-dailyreport.git",
        "version": "1.0.0",
        "author": "内置示例",
        "tags": ["工具"],
    },
]


class PluginMarket:
    """插件市场。"""

    def __init__(self, registry_url: str = "", installer: Any = None, timeout: float = 10.0):
        self.registry_url = registry_url
        self.installer = installer
        self.timeout = timeout
        self._cache: list[dict] = list(_BUILTIN_PLUGINS)

    async def available(self, refresh: bool = False) -> list[dict]:
        """插件列表;refresh=True 时重新拉取远端注册表。"""
        if refresh and self.registry_url:
            try:
                async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
                    resp = await client.get(self.registry_url)
                    resp.raise_for_status()
                    data = resp.json()
                if isinstance(data, dict) and isinstance(data.get("plugins"), list):
                    self._cache = data["plugins"]
                elif isinstance(data, list):
                    self._cache = data
            except Exception as exc:  # noqa: BLE001
                logger.warning("插件市场拉取失败,使用内置列表: {}", exc)
        return list(self._cache)

    async def install(self, name: str) -> dict:
        """按名字安装市场插件(从列表找 source)。"""
        for p in await self.available():
            if p.get("name") == name:
                if self.installer is None:
                    return {"ok": False, "error": "安装器未启用"}
                return await self.installer.install(p.get("source", ""), name=p.get("name"))
        return {"ok": False, "error": f"市场未找到插件: {name}"}
