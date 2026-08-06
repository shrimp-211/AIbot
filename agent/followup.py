"""Follow-up 排队:Agent 处理一条消息期间收到的新消息暂存,完成后入队。

场景:机器人正在思考/执行工具时用户又发来消息,这些消息不被丢弃,
在当前处理完成后按收到顺序交给后续处理(FIFO,每会话上限防积压)。
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field


@dataclass
class FollowUpQueue:
    """按会话的 follow-up 消息队列。"""

    max_per_session: int = 5

    _queues: dict[str, deque] = field(default_factory=dict)

    def enqueue(self, session_id: str, message) -> bool:
        """暂存一条消息;队列已满则丢弃并返回 False。"""
        key = str(session_id)
        q = self._queues.setdefault(key, deque(maxlen=self.max_per_session))
        if len(q) >= self.max_per_session:
            return False  # 已满,丢弃
        q.append(message)
        return True

    def drain(self, session_id: str) -> list:
        """取走当前会话的积压消息(先进先出)。"""
        key = str(session_id)
        q = self._queues.pop(key, None)
        return list(q) if q else []

    def pending(self, session_id: str) -> int:
        q = self._queues.get(str(session_id))
        return len(q) if q else 0
