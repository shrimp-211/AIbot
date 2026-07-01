"""上下文自动压缩 — 参考 AstrBot 和 Claude Code 的实现.

当对话接近模型上下文窗口上限时自动触发压缩:
- 截断策略: 保留最近 N 轮对话，删除旧的
- LLM 压缩策略: 调用 LLM 将历史对话总结为摘要
- 触发阈值: 默认 82% 上下文窗口
"""

from __future__ import annotations

from loguru import logger


class ContextCompressor:
    """上下文压缩器."""

    def __init__(
        self,
        max_tokens: int = 8000,
        trigger_ratio: float = 0.82,
        keep_recent: int = 5,
    ):
        self.max_tokens = max_tokens
        self.trigger_ratio = trigger_ratio
        self.keep_recent = keep_recent

    def should_compress(self, messages: list[dict]) -> bool:
        """检查是否需要压缩."""
        estimated = self._estimate_tokens(messages)
        threshold = int(self.max_tokens * self.trigger_ratio)
        return estimated >= threshold

    def truncate(self, messages: list[dict]) -> list[dict]:
        """截断策略: 保留 system + 最近 N 轮对话."""
        if len(messages) <= self.keep_recent * 2 + 1:
            return messages

        # 保留 system 消息
        system_msgs = [m for m in messages if m["role"] == "system"]
        # 保留最近 N 轮 (user + assistant)
        conversation = [m for m in messages if m["role"] in ("user", "assistant", "tool")]
        recent = conversation[-(self.keep_recent * 2):]

        compressed = system_msgs + [
            {"role": "system", "content": "[更早的对话已被截断以节省上下文]"}
        ] + recent

        logger.info(
            f"上下文截断: {len(messages)} → {len(compressed)} 条消息 "
            f"(保留最近 {self.keep_recent} 轮)"
        )
        return compressed

    async def summarize(
        self,
        messages: list[dict],
        provider,
        summary_prompt: str | None = None,
    ) -> list[dict]:
        """LLM 压缩策略: 将历史对话总结为摘要."""
        if len(messages) <= self.keep_recent * 2 + 1:
            return messages

        system_msgs = [m for m in messages if m["role"] == "system"]
        conversation = [m for m in messages if m["role"] in ("user", "assistant", "tool")]

        # 取中间部分进行摘要
        to_summarize = conversation[:-(self.keep_recent * 2)]
        recent = conversation[-(self.keep_recent * 2):]

        if not to_summarize:
            return messages

        if summary_prompt is None:
            summary_prompt = (
                "请用 200 字以内总结以下对话的关键信息，保留重要的事实、决定和用户偏好:\n\n"
            )

        summary_input = summary_prompt
        for msg in to_summarize:
            role = "用户" if msg["role"] == "user" else "助手"
            content = msg.get("content", "")
            if content:
                summary_input += f"{role}: {content[:300]}\n"

        try:
            summary_messages = [
                {"role": "system", "content": "你是一个对话摘要助手。"},
                {"role": "user", "content": summary_input},
            ]
            resp = await provider.chat(summary_messages, tools=None)
            summary = resp.get("content", "对话摘要生成失败")[:500]
        except Exception:
            logger.exception("摘要生成失败，回退到截断策略")
            return self.truncate(messages)

        compressed = system_msgs + [
            {"role": "system", "content": f"[对话历史摘要]\n{summary}"}
        ] + recent

        logger.info(
            f"LLM 压缩: {len(messages)} → {len(compressed)} 条消息"
        )
        return compressed

    async def compress(
        self,
        messages: list[dict],
        provider,
        strategy: str = "auto",
    ) -> list[dict]:
        """自动选择压缩策略.

        Args:
            messages: 消息列表
            provider: LLM 提供商 (用于摘要策略)
            strategy: 'truncate' | 'summarize' | 'auto'

        Returns:
            压缩后的消息列表
        """
        if not self.should_compress(messages):
            return messages

        if strategy == "truncate":
            return self.truncate(messages)
        elif strategy == "summarize":
            return await self.summarize(messages, provider)
        else:
            # auto: 优先摘要，失败则截断
            try:
                return await self.summarize(messages, provider)
            except Exception:
                logger.warning("摘要压缩失败，回退到截断")
                return self.truncate(messages)

    @staticmethod
    def _estimate_tokens(messages: list[dict]) -> int:
        """启发式 Token 估算 (参考 AstrBot: 中文 0.6 token/字, 非中文 0.3 token/字)."""
        total = 0
        for msg in messages:
            content = msg.get("content", "") or ""
            if isinstance(content, str):
                chinese = sum(1 for c in content if "一" <= c <= "鿿")
                non_chinese = len(content) - chinese
                total += int(chinese * 0.6 + non_chinese * 0.3)
            if msg.get("tool_calls"):
                total += 200  # tool_calls 开销
        return total
