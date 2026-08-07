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


def should_compress(
    messages: list[dict],
    max_tokens: int,
    threshold: float = 0.82,
    trusted_tokens: int = 0,
) -> bool:
    """判断是否需压缩。

    trusted_tokens>0 时优先使用 LLM API 报告的实际输入 token(参照 AstrBot
    trusted_token_usage),更准确;否则回退字符估算。
    """
    if trusted_tokens > 0:
        return trusted_tokens >= max_tokens * threshold
    total = sum(estimate_tokens(m.get("content") or "") for m in messages)
    return total >= max_tokens * threshold


_SUMMARY_INSTRUCTION = (
    "基于我们的完整对话历史,产出一份简洁的要点摘要,以便无缝继续后续工作:\n"
    "1. 系统覆盖讨论过的核心话题及各自的最终结论,明确标出当前最新重点。\n"
    "2. 若使用了任何工具,总结工具调用情况(总次数),并提取最有价值的工具输出要点。\n"
    "3. 若阅读过文件/文档/代码/参考资料且对后续工作有用,逐一列出其范围与路径。\n"
    "4. 若有初始用户目标,先说明目标,再描述当前进度/状态。\n"
    "5. 若任务仍在进行中,以最新已知结果和具体的下一步结尾。\n"
    "请使用用户的语言书写。只输出摘要内容,不要任何额外文字或格式。"
)


def _split_into_rounds(messages: list[dict]) -> list[list[dict]]:
    """按轮次切分:每个 user 消息开启新一轮,收集到下一个 user 前结束。

    保持 user-assistant 轮次边界,保证摘要不切断对话回合。
    """
    rounds: list[list[dict]] = []
    cur: list[dict] = []
    for m in messages:
        if m.get("role") == "user" and cur:
            rounds.append(cur)
            cur = []
        cur.append(m)
    if cur:
        rounds.append(cur)
    return rounds


async def compress_messages(
    provider: Any,
    messages: list[dict],
    max_tokens: int,
    keep_recent_ratio: float = 0.15,
    do_summarize: bool = True,
) -> list[dict]:
    """压缩消息列表(参照 AstrBot LLMSummaryCompressor)。

    - 按轮次切分,用 token 预算比例(keep_recent_ratio)决定保留多少"精确"的最近轮次,
      其余旧轮次交给 LLM 摘要;摘要指令覆盖工具使用/文件阅读/用户目标/任务续接
    - LLM 失败或禁用时回退为截断(仅保留最近轮次)
    - 输出统一过 `_normalize`:修复 tool 配对,并保证 system 后紧跟 user
    """
    if not messages or not should_compress(messages, max_tokens):
        return messages

    rounds = _split_into_rounds(messages)
    total_tokens = sum(estimate_tokens(m.get("content") or "") for m in messages)
    budget = max(1, int(total_tokens * min(max(float(keep_recent_ratio), 0.0), 0.3)))

    # 从最新往前累积,直到超过 token 预算(最新一轮总是保留)
    recent_rounds: list[list[dict]] = []
    used = 0
    for rnd in reversed(rounds):
        rt = sum(estimate_tokens(m.get("content") or "") for m in rnd)
        if recent_rounds and used + rt > budget:
            break
        recent_rounds.insert(0, rnd)
        used += rt
    # 用对象身份(id)区分新旧轮次:轮次可能内容相同(如重复的纯文本消息),
    # 值相等判断会把旧轮次误判为"已保留",导致永不压缩。
    recent_ids = {id(r) for r in recent_rounds}
    old_rounds = [r for r in rounds if id(r) not in recent_ids]
    if not old_rounds:
        return messages  # 无可压缩内容

    recent_msgs = [m for r in recent_rounds for m in r]
    merged: list[dict] | None = None
    if do_summarize and provider is not None:
        try:
            old_msgs = [m for r in old_rounds for m in r]
            raw = "\n".join(
                f"{m.get('role')}: {str(m.get('content', ''))[:300]}" for m in old_msgs
            )
            result = await provider.chat(
                [
                    {
                        "role": "user",
                        "content": (
                            "请将我们的对话历史压缩成一段简洁摘要。\n"
                            f"<extra_instruction>\n{_SUMMARY_INSTRUCTION}\n"
                            "</extra_instruction>\n"
                            "以下是需要压缩的历史:\n\n" + raw[:12000]
                        ),
                    }
                ],
                system_prompt="你是对话摘要助手,只输出摘要本身。",
            )
            summary = (result.get("content") or "").strip()
            if summary:
                # user/assistant 摘要对(参照 AstrBot),兼容性优于 system 注入
                merged = [
                    {"role": "user", "content": f"我们之前的对话历史摘要: {summary}"},
                    {"role": "assistant", "content": "已确认上述对话历史摘要。"},
                ] + recent_msgs
        except Exception:  # noqa: BLE001
            pass

    if merged is None:
        merged = recent_msgs
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
