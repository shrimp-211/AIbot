"""第 1 阶段: 唤醒 + 白名单检查 — 参考 AstrBot WakingCheckStage."""

from loguru import logger


class WakeCheckStage:
    """检查是否需要唤醒机器人 + 白名单校验.

    参考 AstrBot 的 WakingCheckStage + WhitelistCheckStage 设计:
    触发条件 (任一满足):
    - 私聊消息: 总是响应
    - 群聊 @机器人
    - 以配置的唤醒词开头
    - 以命令前缀开头 (/help 等)
    - 消息匹配已注册的插件命令

    白名单:
    - group_whitelist 为空 → 所有群允许
    - 否则只有白名单中的群才允许响应
    """

    def __init__(self, auth, cfg):
        self.auth = auth
        self.cfg = cfg

    async def process(self, event):
        """检查是否应该响应."""
        # 白名单检查 (群聊)
        if event.is_group:
            whitelist = self.cfg.get("permissions.group_whitelist", [])
            if whitelist and event.group_id not in whitelist:
                logger.debug(f"群不在白名单中: {event.group_id}")
                event.stop()
                return

        # 私聊始终响应
        if event.is_private:
            return

        # 群聊: 检查 @ 或唤醒词
        if event.is_group:
            if event.is_tome:
                return

            wake_words = self.cfg.get("agent.wake_words", [])
            for word in wake_words:
                if event.text.strip().startswith(word):
                    return

            # 插件命令匹配 (任何以命令前缀开头的消息)
            prefix = self.cfg.get("permissions.command_prefix", "/")
            if event.text.strip().startswith(prefix):
                return

            # 不满足任何条件
            logger.debug(f"未唤醒: {event.text[:50]}")
            event.stop()
