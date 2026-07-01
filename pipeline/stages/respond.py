"""第 7 阶段: 发送回复 — 通过适配器将结果发送到 QQ."""

import asyncio

from loguru import logger


class RespondStage:
    """最终回复发送阶段."""

    def __init__(self, adapter=None):
        self.adapter = adapter

    def set_adapter(self, adapter):
        self.adapter = adapter

    async def process(self, event):
        """发送回复消息."""
        reply = event.get_reply()
        if not reply:
            return

        segments = event.state.get("reply_segments", [reply])
        adapter = self.adapter

        if adapter is None:
            logger.error("适配器未注入，无法发送消息")
            return

        # 逐段发送 (每段间隔 300ms 防止刷屏)
        for i, seg in enumerate(segments):
            success = await adapter.send(event, seg)
            if not success:
                logger.error(f"发送第 {i+1} 段失败")
            if i < len(segments) - 1:
                await asyncio.sleep(0.3)
