"""QQ 适配器层 — 基于 NoneBot2 适配器模式，封装 OneBot v11 协议."""

from .onebot_v11 import OneBotV11Adapter
from .event import AgentEvent
from .message import MessageChain, MessageSegment

__all__ = ["OneBotV11Adapter", "AgentEvent", "MessageChain", "MessageSegment"]
