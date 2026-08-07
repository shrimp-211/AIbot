"""Star 事件处理器注册表(compat stub)。"""

from __future__ import annotations

import enum


class EventType(enum.Enum):
    """表示一个 AstrBot 内部事件的类型(与 AstrBot 原版一致)。"""

    OnAstrBotLoadedEvent = enum.auto()  # AstrBot 加载完成
    OnPlatformLoadedEvent = enum.auto()  # 平台加载完成

    AdapterMessageEvent = enum.auto()  # 收到适配器发来的消息
    OnWaitingLLMRequestEvent = enum.auto()  # 等待调用 LLM(在获取锁之前,仅通知)
    OnLLMRequestEvent = enum.auto()  # 收到 LLM 请求(可以是用户也可以是插件)
    OnLLMResponseEvent = enum.auto()  # LLM 响应后
    OnAgentBeginEvent = enum.auto()  # Agent 开始运行
    OnAgentDoneEvent = enum.auto()  # Agent 运行完成
    OnDecoratingResultEvent = enum.auto()  # 发送消息前
    OnCallingFuncToolEvent = enum.auto()  # 调用函数工具
    OnUsingLLMToolEvent = enum.auto()  # 使用 LLM 工具
    OnLLMToolRespondEvent = enum.auto()  # 调用函数工具后
    OnAfterMessageSentEvent = enum.auto()  # 发送消息后
    OnPluginErrorEvent = enum.auto()  # 插件处理消息异常时
    OnPluginLoadedEvent = enum.auto()  # 插件加载完成
    OnPluginUnloadedEvent = enum.auto()  # 插件卸载完成


class StarHandlerType(enum.Enum):
    Event = "event"
    LlmFunction = "llm_function"
    Decorator = "decorator"
    General = "general"


star_handlers_registry: list = []


class StarHandler:
    def __init__(self, handler_type: StarHandlerType, **kwargs) -> None:
        self.handler_type = handler_type
        self.event_type = kwargs.get("event_type")
        self.filters = kwargs.get("filters", [])
        self.handler_full_name = kwargs.get("handler_full_name", "")
        self.event = kwargs.get("event")
        self.config = kwargs.get("config")
