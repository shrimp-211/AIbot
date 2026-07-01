"""统一事件模型 — 平台无关的消息事件抽象 (参考 NoneBot2 Event)."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .message import MessageChain


class EventType(Enum):
    MESSAGE = "message"
    NOTICE = "notice"
    REQUEST = "request"
    META = "meta_event"


class MessageType(Enum):
    PRIVATE = "private"
    GROUP = "group"


@dataclass
class AgentEvent:
    """平台无关的统一事件模型.

    所有 IM 平台消息经适配器转换后统一为此格式，
    后续管道和 Agent 引擎仅依赖此模型。
    """

    type: EventType = EventType.MESSAGE
    """事件类型"""

    message_type: MessageType = MessageType.GROUP
    """消息类型 (群聊/私聊)"""

    user_id: str = ""
    """发送者 QQ 号"""

    user_name: str = ""
    """发送者昵称"""

    group_id: str = ""
    """群号 (私聊时为空)"""

    message_id: str = ""
    """消息 ID"""

    message: str = ""
    """纯文本消息内容"""

    message_chain: MessageChain = field(default_factory=MessageChain)
    """消息段序列 (含多模态内容)"""

    raw_event: dict[str, Any] = field(default_factory=dict)
    """原始事件数据 (平台特定)"""

    is_tome: bool = False
    """是否 @了机器人"""

    session_id: str = ""
    """会话标识符 (用于记忆隔离)"""

    state: dict[str, Any] = field(default_factory=dict)
    """管道状态字典 (在阶段间传递数据)"""

    _reply: str | None = field(default=None, repr=False, init=False)
    _stopped: bool = field(default=False, repr=False, init=False)

    # ---- 回复控制 ----

    def reply(self, text: str) -> None:
        self._reply = text

    def get_reply(self) -> str | None:
        return self._reply

    def stop(self) -> None:
        """终止管道传播."""
        self._stopped = True

    def is_stopped(self) -> bool:
        return self._stopped

    # ---- 便捷属性 ----

    @property
    def is_group(self) -> bool:
        return self.message_type == MessageType.GROUP

    @property
    def is_private(self) -> bool:
        return self.message_type == MessageType.PRIVATE

    @property
    def text(self) -> str:
        """获取纯文本 (去除 CQ 码后的内容)."""
        if self.message_chain:
            return self.message_chain.extract_text()
        return self.message

    def get_session_id(self) -> str:
        """生成会话唯一标识."""
        if self.is_group:
            return f"group_{self.group_id}_{self.user_id}"
        return f"private_{self.user_id}"

    def __repr__(self) -> str:
        loc = f"group_{self.group_id}" if self.is_group else f"private_{self.user_id}"
        return f"<AgentEvent {self.type.value} {loc} user={self.user_id} text={self.text[:50]!r}>"
