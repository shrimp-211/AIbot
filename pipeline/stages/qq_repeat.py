"""QQ 复读检测阶段:群内同消息连续 N 次且机器人被唤醒时参与复读。

对所有群消息登记样本(供复读统计);仅当 @ 到机器人/被唤醒且触发阈值时
回复复读文本并标记事件停止(跳过后续 Agent 处理)。
"""
from __future__ import annotations

from loguru import logger

from ...adapter.event import AgentEvent
from ..scheduler import Stage


class QqRepeatStage(Stage):
    def __init__(self, persona, threshold: int = 3, enabled: bool = True):
        self.persona = persona
        self.threshold = max(2, int(threshold))
        self.enabled = bool(enabled)

    async def process(self, event: AgentEvent) -> None:
        if not self.enabled or event.message_type != "group" or not event.group_id:
            return
        if self.persona is None:
            return
        text = event.plain_text or ""
        # 登记样本(所有群消息都登记,与是否唤醒无关)
        self.persona.add_sample(event.group_id, text)
        # 仅唤醒/被 @ 时参与复读,避免主动刷屏
        if event.is_tome and self.persona.should_repeat(event.group_id, text):
            logger.info("触发复读: 群 {} 重复「{}」", event.group_id, text[:20])
            await event.reply(text)
            event.stop()  # 跳过后续 Agent 处理
