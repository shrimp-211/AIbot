"""平台抽象(compat stub)。"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Platform:
    """平台适配器抽象基类(本项目通过 adapter_registry 管理平台)。"""

    config: dict = field(default_factory=dict)

    def __init__(self, config: dict | None = None) -> None:
        self.config = config or {}


from astrbot.core.platform.message_session import MessageSesion, MessageSession  # noqa: E402
from astrbot.core.platform.message_type import MessageType  # noqa: E402

__all__ = ["Platform", "MessageSesion", "MessageSession", "MessageType"]
