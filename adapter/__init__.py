"""适配器包:多平台协议接入。

- BaseAdapter / AdapterRegistry:统一抽象层
- OneBotV11Adapter:反向 WebSocket(服务端,多连接)
- OneBotV11Client:正向 WebSocket(客户端,自动重连)
- OneBotV11Http:HTTP 上报 + HTTP API
- QQOfficialAdapter:QQ 官方开放平台 Webhook
"""
from .base import AdapterRegistry, BaseAdapter
from .event import AgentEvent
from .message import MessageChain, MessageSegment
from .message_universal import UniMessage, UniSegment
from .onebot_forward import OneBotV11Client
from .onebot_http import OneBotV11Http
from .onebot_v11 import OneBotV11Adapter
from .qq_official import QQOfficialAdapter

__all__ = [
    "AdapterRegistry",
    "BaseAdapter",
    "AgentEvent",
    "MessageChain",
    "MessageSegment",
    "UniMessage",
    "UniSegment",
    "OneBotV11Adapter",
    "OneBotV11Client",
    "OneBotV11Http",
    "QQOfficialAdapter",
]
