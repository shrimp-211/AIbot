"""预处理阶段:提取多模态内容(图片/语音)与上下文标记。"""
from __future__ import annotations

from ...adapter.event import AgentEvent
from ..scheduler import Stage


class PreProcessStage(Stage):
    async def process(self, event: AgentEvent) -> None:
        images = [
            s.data.get("url") or s.data.get("file")
            for s in event.message.get("image")
            if s.data.get("url") or s.data.get("file")
        ]
        records = [
            s.data.get("file")
            for s in event.message.get("record")
            if s.data.get("file")
        ]
        event.state["images"] = images
        event.state["records"] = records
        event.state["has_image"] = bool(images)
        event.state["has_voice"] = bool(records)
