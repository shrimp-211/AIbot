"""处理阶段(Agent 引擎):仅为唤醒消息生成回复。

插件分发已前置到 PluginStage,本阶段只负责把通过唤醒检测的消息
交给 Agent 引擎处理。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from ...adapter.event import AgentEvent
from ..scheduler import Stage

if TYPE_CHECKING:
    from ...agent.engine import AgentEngine


class ProcessStage(Stage):
    def __init__(self, agent_engine: "AgentEngine"):
        self._engine = agent_engine

    async def process(self, event: AgentEvent) -> None:
        reply = await self._engine.process(event)
        if reply:
            event.state["reply"] = reply
