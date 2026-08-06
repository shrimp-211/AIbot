"""OneBot v11 事件构建共享实现。

反向 WS / 正向 WS / HTTP 三个 OneBot 系适配器的事件构建逻辑完全一致,
抽到此处避免三份重复。差异仅在 `platform` 与发送回调(`_send_callback`)。
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable

from .event import AgentEvent
from .message import MessageChain

SendCallback = Callable[["AgentEvent", str, bool], Awaitable[None]]


def build_message_event(
    platform: str,
    self_id: str,
    data: dict[str, Any],
    send_callback: SendCallback,
) -> AgentEvent:
    mtype = data.get("message_type", "group")
    user_id = str(data.get("user_id", ""))
    group_id = str(data.get("group_id", "")) if data.get("group_id") else None
    sender = data.get("sender") or {}
    message = data.get("message", [])
    raw = data.get("raw_message", "")
    self_id = str(data.get("self_id", self_id))

    if isinstance(message, str):
        chain = MessageChain.from_cq_string(message)
    else:
        chain = MessageChain.from_segments(message)

    is_tome = False
    if mtype == "group":
        at_qs = [str(s.data.get("qq")) for s in chain.get("at")]
        is_tome = self_id in at_qs or "all" in at_qs

    session_id = group_id if mtype == "group" else user_id
    return AgentEvent(
        platform=platform,
        message_type=mtype,
        group_id=group_id,
        user_id=user_id,
        sender_name=sender.get("nickname", "") or str(user_id),
        sender_role=sender.get("role", ""),
        message=chain,
        raw_message=raw,
        message_id=data.get("message_id"),
        session_id=session_id,
        is_tome=is_tome,
        _send_callback=send_callback,
    )


def build_notice_event(
    platform: str, data: dict[str, Any], send_callback: SendCallback
) -> AgentEvent:
    """构造通知事件(group_increase/group_decrease/group_recall/...)。"""
    group_id = str(data.get("group_id", "")) if data.get("group_id") else None
    user_id = str(data.get("user_id", ""))
    return AgentEvent(
        platform=platform,
        event_type="notice",
        notice_type=data.get("notice_type", ""),
        sub_type=data.get("sub_type", ""),
        operator_id=str(data.get("operator_id", "") or ""),
        group_id=group_id,
        user_id=user_id,
        message_id=data.get("message_id"),
        session_id=group_id or user_id,
        _send_callback=send_callback,
    )


def build_request_event(
    platform: str, data: dict[str, Any], send_callback: SendCallback
) -> AgentEvent:
    """构造请求事件(friend 加好友 / group 加群)。flag 用于审批回执。"""
    group_id = str(data.get("group_id", "")) if data.get("group_id") else None
    user_id = str(data.get("user_id", ""))
    request_type = data.get("request_type", "")
    return AgentEvent(
        platform=platform,
        event_type="request",
        notice_type=request_type,  # friend | group
        sub_type=data.get("sub_type", ""),  # group: add | invite
        flag=str(data.get("flag", "") or ""),
        group_id=group_id,
        user_id=user_id,
        raw_message=data.get("comment", ""),
        session_id=group_id or user_id,
        _send_callback=send_callback,
    )
