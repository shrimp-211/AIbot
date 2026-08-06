"""插件分发阶段(NoneBot 风格):所有消息先经过插件,插件可自行处理并阻断后续。

放在 WakeCheckStage 之前,因此插件的 message/regex/command handler 能看到
全部消息(无需 @ 唤醒),实现关键词回复、复读机等常驻插件;命令无需 @ 触发。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from ...adapter.event import AgentEvent
from ...utils.config import Config
from ..scheduler import Stage

if TYPE_CHECKING:
    from ...plugins.registry import PluginRegistry


class PluginStage(Stage):
    def __init__(self, registry: "PluginRegistry | None", config: Config):
        self._registry = registry
        self._config = config
        # 命令前缀在启动时固定,缓存避免每条消息重复嵌套查询配置
        self._prefixes: tuple[str, ...] = tuple(
            config.get("pipeline.command_prefixes", ["!", "/"])
        )

    async def process(self, event: AgentEvent) -> None:
        # 命令前缀标记(供插件与唤醒检测复用)
        text = event.plain_text.strip()
        event.is_plain_command = text.startswith(self._prefixes)

        if self._registry is None:
            return
        handled = await self._registry.dispatch(event)
        if handled:
            event.stop()
