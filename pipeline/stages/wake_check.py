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

        # 私聊:配对审批(参考 OpenClaw pairing),管理员/已批准用户直接通过
        if event.message_type == "private":
            pairing_enabled = bool(self._config.get("security.pairing_enabled", False))
            if pairing_enabled and not self._auth.is_admin_or_super(event.user_id):
                if self._auth.is_paired(event.user_id):
                    return
                if self._auth.has_pending(event.user_id):
                    await event.reply("你的配对申请已提交,请等待管理员审批。")
                else:
                    code = self._auth.request_pairing(event.user_id)
                    await event.reply(
                        f"🔐 你是未配对用户。配对码: {code}\n"
                        "请管理员执行 `/approve <配对码>` 批准后即可使用本助手。"
                    )
                event.stop()
                return
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
