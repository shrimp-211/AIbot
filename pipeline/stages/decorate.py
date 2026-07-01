"""第 6 阶段: 结果装饰 — 长文本分段、添加前缀等."""

from loguru import logger


class DecorateStage:
    """结果后处理."""

    def __init__(self, cfg):
        self.cfg = cfg
        self._max_len = cfg.get("security.max_message_length", 2000)

    async def process(self, event):
        reply = event.get_reply()
        if not reply:
            return

        # 截断过长消息 (按自然段分段)
        if len(reply) > self._max_len:
            segments = self._split_long_message(reply)
            # 将分段存入 state，RespondStage 会逐个发送
            event.state["reply_segments"] = segments
            event.reply(segments[0])  # 首段作为直接回复
        else:
            event.state["reply_segments"] = [reply]

    def _split_long_message(self, text: str) -> list[str]:
        """将长消息按自然段分段."""
        limit = self._max_len
        if len(text) <= limit:
            return [text]

        segments = []
        paragraphs = text.split("\n\n")
        current = ""

        for para in paragraphs:
            if len(current) + len(para) + 2 <= limit:
                current = (current + "\n\n" + para) if current else para
            else:
                if current:
                    segments.append(current)
                # 如果单个段落超限，按字符切分
                if len(para) > limit:
                    for i in range(0, len(para), limit):
                        segments.append(para[i:i + limit])
                    current = ""
                else:
                    current = para

        if current:
            segments.append(current)

        return segments or [text[:limit]]
