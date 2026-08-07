"""Agent 消息模型(精简兼容)。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

CHECKPOINT_MARKER = "__astrbot_checkpoint__"


@dataclass
class Message:
    role: str = "user"
    content: list | str | None = None
    name: str | None = None
    id: str | None = None
    check_point: str | None = None
    tool_calls: list | None = None
    tool_call_id: str | None = None

    def to_dict(self) -> dict:
        return {
            "role": self.role,
            "content": self.content,
            "name": self.name,
            "id": self.id,
            "check_point": self.check_point,
            "tool_calls": self.tool_calls,
            "tool_call_id": self.tool_call_id,
        }


@dataclass
class UserMessage(Message):
    role: str = "user"


@dataclass
class AssistantMessage(Message):
    role: str = "assistant"


class UserMessageSegment:
    """用户消息段(检查点/对话窗口内)。"""

    def __init__(self, type: str = "plain", text: str | None = None, **kwargs) -> None:
        self.type = type
        self.text = text or ""
        self.kwargs = kwargs

    def to_dict(self) -> dict:
        d: dict = {"type": self.type, "text": self.text}
        d.update(self.kwargs)
        return d


class AssistantMessageSegment:
    """助手消息段(检查点/对话窗口内)。"""

    def __init__(self, type: str = "plain", text: str | None = None, **kwargs) -> None:
        self.type = type
        self.text = text or ""
        self.kwargs = kwargs

    def to_dict(self) -> dict:
        d: dict = {"type": self.type, "text": self.text}
        d.update(self.kwargs)
        return d


def is_checkpoint_message(message) -> bool:
    """判断消息是否为检查点消息(工具调用边界)。"""
    return getattr(message, "check_point", None) is not None


def get_checkpoint_id(message) -> str | None:
    return getattr(message, "check_point", None)
