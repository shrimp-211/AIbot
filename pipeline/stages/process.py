"""处理阶段(核心):先尝试插件匹配,无匹配则交给 Agent 引擎。"""
from __future__ import annotations

from typing import TYPE_CHECKING

from ...adapter.event import AgentEvent
from ..scheduler import Stage

if TYPE_CHECKING:
    from ...agent.engine import AgentEngine
    from ...plugins.registry import PluginRegistry


class ProcessStage(Stage):
    def __init__(self, plugin_registry: "PluginRegistry | None", agent_engine: "AgentEngine"):
        self._registry = plugin_registry
        self._engine = agent_engine

    async def process(self, event: AgentEvent) -> None:
        # 插件优先(插件可自行回复并 stop)
        if self._registry is not None:
            handled = await self._registry.dispatch(event)
            if handled:
                event.stop()
                return
        # Agent 引擎
        if not event.is_stopped:
            reply = await self._engine.process(event)
            if reply:
                event.state["reply"] = reply
