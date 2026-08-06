"""平台无关的统一事件模型 AgentEvent。

通过 dataclass 承载消息上下文,state 字典供管道各阶段传递数据,
reply()/stop() 提供控制接口。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from .message import MessageChain

SendCallback = Callable[["AgentEvent", str], Awaitable[None]]


@dataclass
class AgentEvent:
    platform: str = "qq"
    event_type: str = "message"  # message | notice | request
    message_type: str = "group"  # group | private
    notice_type: str = ""  # group_increase/group_decrease/group_recall/...
    sub_type: str = ""  # leave|kick|disband / set|unset / add|invite
    operator_id: str = ""  # 操作者 QQ(notice 事件)
    flag: str = ""  # request 事件审批标记
    group_id: str | None = None
    user_id: str = ""
    sender_name: str = ""
    sender_role: str = ""  # owner | admin | member
    message: MessageChain = field(default_factory=MessageChain)
    raw_message: str = ""
    message_id: int | None = None
    session_id: str = ""
    is_tome: bool = False
    is_plain_command: bool = False
    state: dict[str, Any] = field(default_factory=dict)

    _send_callback: SendCallback | None = field(default=None, repr=False, compare=False)
    _stopped: bool = field(default=False, repr=False, compare=False)

    @property
    def plain_text(self) -> str:
        return self.message.extract_plain_text()

    async def reply(self, text: str, at: bool = False) -> None:
        """发送回复。at=True 时在群聊中 @ 发送者。"""
        if self._send_callback is not None:
            await self._send_callback(self, text, at=at)

    def stop(self) -> None:
        """终止后续管道阶段。"""
        self._stopped = True

    @property
    def is_stopped(self) -> bool:
        return self._stopped
