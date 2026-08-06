"""上下文压缩:超阈值自动触发,截断旧消息或 LLM 摘要。

中文约 0.6 token/字,英文约 0.25 token/词(粗略估算,用于触发阈值)。
"""
from __future__ import annotations

import json
from typing import Any


def estimate_tokens(text: str) -> int:
    """粗略估算文本 token 数(中文字/词混合)。"""
    if not text:
        return 0
    cjk = sum(1 for ch in text if "一" <= ch <= "鿿")
    other = len(text) - cjk
    return int(cjk * 0.6 + other * 0.25)


def should_compress(messages: list[dict], max_tokens: int, threshold: float = 0.82) -> bool:
    total = sum(estimate_tokens(m.get("content") or "") for m in messages)
    return total >= max_tokens * threshold


async def compress_messages(
    provider: Any,
    messages: list[dict],
    max_tokens: int,
    keep_recent: int = 4,
    do_summarize: bool = True,
) -> list[dict]:
    """压缩消息列表。

    - 保留最近 keep_recent 条
    - 若 do_summarize 且 provider 可用,将旧消息交给 LLM 生成摘要
    - 否则直接截断
    """
    if not messages:
        return messages
    if not should_compress(messages, max_tokens):
        return messages

    old = messages[: -keep_recent] if len(messages) > keep_recent else []
    recent = messages[-keep_recent:]

    if not old:
        return messages

    if do_summarize:
        try:
            raw = "\n".join(
                f"{m.get('role')}: {str(m.get('content', ''))[:300]}" for m in old
            )
            result = await provider.chat(
                [
                    {
                        "role": "user",
                        "content": (
                            "请将下面的对话历史压缩成一段简洁的摘要(保留关键事实、"
                            "用户需求、未完成任务),200字以内:\n\n" + raw[:6000]
                        ),
                    }
                ],
                system_prompt="你是对话摘要助手,只输出摘要本身。",
            )
            summary = (result.get("content") or "").strip()
            if summary:
                return [
                    {
                        "role": "system",
                        "content": f"[历史对话摘要] {summary}",
                    }
                ] + recent
        except Exception:  # noqa: BLE001
            pass

    return recent


def compress_tool_result(result: str, limit: int = 4000, max_chars: int = 12000) -> str:
    """压缩工具返回结果:过长时截断并保留头尾关键信息。

    保留结果开头完整内容 + 结尾摘要信息,中间用省略标记连接。
    """
    if len(result) <= limit:
        return result
    if len(result) <= max_chars:
        return result[:limit]
    head = result[: limit - 200]
    tail = result[-200:]
    return f"{head}\n...[结果过长,已截断,共 {len(result)} 字符]...\n{tail}"
