"""上下文压缩:超阈值自动触发,截断旧消息或 LLM 摘要。

中文约 0.6 token/字,英文约 0.25 token/词(粗略估算,用于触发阈值)。
压缩/截断后统一跑 fix_messages 修复 tool call/tool response 配对(参照
AstrBot ContextTruncator),避免截断破坏 OpenAI 消息格式合法性。
"""
from __future__ import annotations

from typing import Any


def estimate_tokens(text: str) -> int:
    """粗略估算文本 token 数(中文字/词混合)。"""
    if not text:
        return 0
    cjk = sum(1 for ch in text if "一" <= ch <= "鿿")
    other = len(text) - cjk
    return int(cjk * 0.6 + other * 0.25)


# ---------- 消息结构修复(AstrBot ContextTruncator) ----------


def fix_messages(messages: list[dict]) -> list[dict]:
    """修复 tool call / tool response 配对。

    OpenAI 规范要求 assistant(tool_calls) 后必须紧跟对应 tool 消息;
    孤立的 tool 消息(前面无 assistant(tool_calls))会被丢弃,未配对的
    assistant(tool_calls) 也会被移除,避免向 API 发送非法消息序列。
    """
    fixed: list[dict] = []
    pending_assistant: dict | None = None
    pending_tools: list[dict] = []

    def flush_if_valid() -> None:
        nonlocal pending_assistant, pending_tools
        if pending_assistant is not None and pending_tools:
            fixed.append(pending_assistant)
            fixed.extend(pending_tools)
        pending_assistant = None
        pending_tools = []

    for msg in messages:
        role = msg.get("role")
        if role == "tool":
            if pending_assistant is not None:
                pending_tools.append(msg)
            continue
        if role == "assistant" and msg.get("tool_calls"):
            flush_if_valid()
            pending_assistant = msg
            continue
        flush_if_valid()
        fixed.append(msg)
    flush_if_valid()
    return fixed


def _split_system_rest(messages: list[dict]) -> tuple[list[dict], list[dict]]:
    first_non_system = 0
    for i, msg in enumerate(messages):
        if msg.get("role") != "system":
            first_non_system = i
            break
    return messages[:first_non_system], messages[first_non_system:]


def _ensure_user_message(
    system_messages: list[dict],
    truncated: list[dict],
    original: list[dict],
) -> list[dict]:
    """保证 system 后紧跟 user 消息(部分 API 强制要求)。"""
    if truncated and truncated[0].get("role") == "user":
        return system_messages + truncated
    first_user = next((m for m in original if m.get("role") == "user"), None)
    if first_user is None:
        return system_messages + truncated
    return system_messages + [first_user] + truncated


def truncate_by_turns(
    messages: list[dict],
    keep_most_recent_turns: int,
    drop_turns: int = 1,
) -> list[dict]:
    """按轮次截断:保留最近 N 轮(user+assistant 为一轮),丢弃最旧轮次。"""
    if keep_most_recent_turns == -1:
        return messages
    system, rest = _split_system_rest(messages)
    if len(rest) // 2 <= keep_most_recent_turns:
        return messages
    num_to_keep = keep_most_recent_turns - drop_turns + 1
    truncated = rest[-num_to_keep * 2 :] if num_to_keep > 0 else []
    index = next((i for i, m in enumerate(truncated) if m.get("role") == "user"), None)
    if index is not None and index > 0:
        truncated = truncated[index:]
    return fix_messages(_ensure_user_message(system, truncated, messages))


def truncate_by_halving(messages: list[dict]) -> list[dict]:
    """减半截断:删除最旧一半消息,保留最近一半(压缩后仍超限的兜底)。"""
    if len(messages) <= 2:
        return messages
    system, rest = _split_system_rest(messages)
    to_delete = len(rest) // 2
    if to_delete == 0:
        return messages
    truncated = rest[to_delete:]
    index = next((i for i, m in enumerate(truncated) if m.get("role") == "user"), None)
    if index is not None:
        truncated = truncated[index:]
    return fix_messages(_ensure_user_message(system, truncated, messages))


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
    - 输出统一过 `_normalize`:修复 tool 配对,并保证 system 后紧跟 user
      (部分 API 强制要求,参照 AstrBot ContextTruncator._ensure_user_message)
    """
    if not messages:
        return messages
    if not should_compress(messages, max_tokens):
        return messages

    old = messages[: -keep_recent] if len(messages) > keep_recent else []
    recent = messages[-keep_recent:]

    if not old:
        return messages

    merged: list[dict] | None = None
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
                merged = [
                    {
                        "role": "system",
                        "content": f"[历史对话摘要] {summary}",
                    }
                ] + recent
        except Exception:  # noqa: BLE001
            pass

    if merged is None:
        merged = recent
    return _normalize(merged, messages)


def _normalize(merged: list[dict], original: list[dict]) -> list[dict]:
    """修复 tool 配对 + 保证 system 后紧跟 user(参照 AstrBot ContextTruncator)。"""
    system, rest = _split_system_rest(merged)
    return fix_messages(_ensure_user_message(system, rest, original))


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
