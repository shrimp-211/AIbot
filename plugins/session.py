"""会话控制系统 — 参考 NoneBot2 的 got/pause/reject/finish 机制.

实现多轮对话控制流程，使插件可以:
- got(key, prompt): 向用户提问并等待回复
- receive(id): 等待新事件 (不发送提示)
- pause(prompt): 挂起当前 handler，等待新事件后进入下一个 handler
- reject(prompt): 拒绝当前输入，要求重新输入 (重试同一 handler)
- finish(message): 结束整个处理流程
- skip: 跳过当前 handler

底层通过异常实现流程控制 (参考 NoneBot2):
- PausedException → 挂起
- RejectedException → 重试
- FinishedException → 结束
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# ---- 会话异常 ----

class SessionPaused(Exception):
    """会话挂起 — 等待用户输入后进入下一个 handler."""
    def __init__(self, prompt: str = ""):
        self.prompt = prompt
        super().__init__(prompt)


class SessionRejected(Exception):
    """输入拒绝 — 要求重新输入 (重试当前 handler)."""
    def __init__(self, prompt: str = ""):
        self.prompt = prompt
        super().__init__(prompt)


class SessionFinished(Exception):
    """会话结束."""
    def __init__(self, message: str = ""):
        self.message = message
        super().__init__(message)


class SessionSkipped(Exception):
    """跳过当前 handler."""
    pass


# ---- 会话状态 ----

@dataclass
class SessionState:
    """单个会话的状态管理.

    每个 Matcher 实例维护一个 SessionState，
    存储对话上下文、got 参数、receive 事件等。
    """

    data: dict[str, Any] = field(default_factory=dict)
    """通用状态字典"""

    handler_index: int = 0
    """当前 handler 索引"""

    remain_handlers: list = field(default_factory=list)
    """剩余待执行的 handler"""

    pending_got: str | None = None
    """正在等待 got 的 key"""

    pending_receive: str | None = None
    """正在等待 receive 的 id"""

    got_prompt: str = ""
    """got 的提示消息"""

    reject_count: int = 0
    """连续 reject 次数 (用于检测死循环)"""

    is_active: bool = True
    """会话是否活跃"""

    def get(self, key: str, default=None) -> Any:
        return self.data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self.data[key] = value

    def has(self, key: str) -> bool:
        return key in self.data

    def reset(self) -> None:
        """重置会话状态."""
        self.handler_index = 0
        self.pending_got = None
        self.pending_receive = None
        self.reject_count = 0
        self.is_active = True


# ---- Matcher 会话方法 Mixin ----

class SessionMixin:
    """为 Matcher/Plugin 添加会话控制方法."""

    _state: SessionState

    # ---- got: 向用户提问 ----

    async def got(self, key: str, prompt: str = "") -> Any:
        """向用户提问，等待回复。

        如果 state 中已有 key 的值，直接返回；
        否则发送 prompt 并挂起，等待用户输入。

        Args:
            key: 参数名 (回复存入 state[key])
            prompt: 提问文本

        Returns:
            用户回复的消息文本

        Raises:
            SessionPaused: 挂起等待用户输入
        """
        if self._state.has(key):
            return self._state.get(key)

        self._state.pending_got = key
        self._state.got_prompt = prompt
        raise SessionPaused(prompt)

    def set_arg(self, key: str, value: Any) -> None:
        """手动设置 got 参数 (跳过询问)."""
        self._state.set(key, value)

    def get_arg(self, key: str, default=None) -> Any:
        """获取 got 参数."""
        return self._state.get(key, default)

    # ---- receive: 等待新事件 ----

    async def receive(self, id: str = "") -> Any:
        """等待一个新的消息事件。

        Args:
            id: 事件标识 (存入 state 用)

        Returns:
            新的事件对象
        """
        self._state.pending_receive = id or "__default__"
        raise SessionPaused("")

    # ---- pause: 进入下一个 handler ----

    async def pause(self, prompt: str = "") -> None:
        """结束当前 handler，等待新事件后进入下一个 handler."""
        self._state.pending_receive = "__pause__"
        raise SessionPaused(prompt)

    # ---- reject: 重试当前 handler ----

    async def reject(self, prompt: str = "") -> None:
        """拒绝当前输入，要求重新输入."""
        self._state.reject_count += 1
        if self._state.reject_count > 10:
            raise SessionFinished("重试次数过多，已终止")
        raise SessionRejected(prompt)

    async def reject_arg(self, key: str, prompt: str = "") -> None:
        """拒绝指定的 got 参数."""
        self._state.data.pop(key, None)
        self._state.pending_got = key
        self._state.got_prompt = prompt
        self._state.reject_count += 1
        raise SessionRejected(prompt)

    # ---- finish: 结束会话 ----

    async def finish(self, message: str = "") -> None:
        """结束整个处理流程."""
        self._state.is_active = False
        raise SessionFinished(message)

    # ---- skip ----

    async def skip(self) -> None:
        """跳过当前 handler."""
        raise SessionSkipped()


# ---- 会话管理器 ----

class SessionManager:
    """管理所有活跃的多轮会话.

    按 session_id 追踪活跃会话，处理新消息的路由:
    - 如果有活跃会话 → 恢复执行
    - 如果无活跃会话 → 创建新会话 (匹配新 Matcher)
    """

    def __init__(self):
        self._sessions: dict[str, SessionState] = {}
        self._matchers: dict[str, "Matcher"] = {}

    def get_or_create(self, session_id: str) -> SessionState:
        """获取或创建会话状态."""
        if session_id not in self._sessions:
            self._sessions[session_id] = SessionState()
        return self._sessions[session_id]

    def get_active(self, session_id: str) -> SessionState | None:
        """获取活跃会话 (存在且未结束)."""
        state = self._sessions.get(session_id)
        if state and state.is_active:
            return state
        return None

    def clear(self, session_id: str) -> None:
        """清理会话."""
        self._sessions.pop(session_id, None)

    def clear_all(self) -> None:
        self._sessions.clear()

    def has_active(self, session_id: str) -> bool:
        """检查是否有活跃会话."""
        state = self._sessions.get(session_id)
        return state is not None and state.is_active
