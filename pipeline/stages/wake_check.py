"""唤醒检测阶段:判断消息是否需要 Agent 处理。

黑名单/配对审批/群白名单已抽到 SecurityStage,命令前缀检测已抽到
PluginStage,本阶段仅保留:@ 机器人 / 命令前缀 / 唤醒词。
"""
from __future__ import annotations

from ...adapter.event import AgentEvent
from ...utils.config import Config
from ..scheduler import Stage


class WakeCheckStage(Stage):
    def __init__(self, config: Config):
        self._config = config

    async def process(self, event: AgentEvent) -> None:
        if event.is_tome or event.is_plain_command:
            return
        text = event.plain_text.strip()
        wake_words = self._config.get("pipeline.wake_words", ["机器人", "小助手", "AI"])
        for word in wake_words:
            if word and word in text:
                return
        event.stop()
