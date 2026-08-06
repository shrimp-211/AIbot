"""唤醒检测阶段:黑名单、群白名单、唤醒词/命令前缀/@ 检测。"""
from __future__ import annotations

from ...adapter.event import AgentEvent
from ...security.auth import AuthManager
from ...utils.config import Config
from ..scheduler import Stage


class WakeCheckStage(Stage):
    def __init__(self, config: Config, auth: AuthManager):
        self._config = config
        self._auth = auth

    async def process(self, event: AgentEvent) -> None:
        # 黑名单
        if self._auth.is_blacklisted(event.user_id):
            event.stop()
            return

        # 私聊总是响应
        if event.message_type == "private":
            return

        # 群白名单
        whitelist = self._config.get("pipeline.group_whitelist", [])
        if whitelist:
            allowed = {str(g) for g in whitelist}
            if event.group_id and event.group_id not in allowed:
                event.stop()
                return

        # 命令前缀
        cmd_prefixes = self._config.get("pipeline.command_prefixes", ["!", "/"])
        text = event.plain_text.strip()
        event.is_plain_command = text.startswith(tuple(cmd_prefixes))

        # 唤醒检测:@机器人 / 唤醒词 / 命令前缀
        if event.is_tome or event.is_plain_command:
            return
        wake_words = self._config.get("pipeline.wake_words", ["机器人", "小助手", "AI"])
        for word in wake_words:
            if word and word in text:
                return
        event.stop()
