"""工具基类 + 注册中心 + 权限集成。

每个工具声明 name/description/parameters(JSON Schema)和 permission_level,
执行前经 AuthManager 检查,deny/ask 会抛出 PermissionError。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ...security.auth import AuthManager, Decision

if TYPE_CHECKING:
    from ...adapter.event import AgentEvent
    from ...adapter.onebot_v11 import OneBotV11Adapter
    from ...storage.db import JsonKV
    from ...utils.config import Config


@dataclass
class ToolContext:
    """工具执行上下文,携带运行所需的服务引用。"""

    event: "AgentEvent"
    adapter: "OneBotV11Adapter"
    auth: AuthManager
    config: "Config"
    db: "JsonKV"
    memory: Any = None
    persona_manager: Any = None
    cron_manager: Any = None
    skills: Any = None
    subagent_manager: Any = None
    extra: dict[str, Any] = field(default_factory=dict)


class Tool(ABC):
    name: str = ""
    description: str = ""
    parameters: dict[str, Any] = {"type": "object", "properties": {}}
    permission_level: int = 0  # 0=所有人 1=信任 7=管理员

    def __init__(self, auth: AuthManager | None = None):
        self._auth = auth

    def bind_auth(self, auth: AuthManager) -> None:
        self._auth = auth

    def to_openai_schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }

    def check_permission(self, role_level: int) -> Decision:
        if role_level < self.permission_level:
            return Decision.DENY
        if self._auth is None:
            return Decision.ALLOW
        return self._auth.decide("tool", self.name)

    @abstractmethod
    async def execute(self, ctx: ToolContext, **kwargs: Any) -> Any:
        """执行工具,返回可被序列化为字符串的结果。"""
        raise NotImplementedError


class ToolRegistry:
    """工具注册中心,负责注册/查找/执行 + 权限检查。"""

    def __init__(self, auth: AuthManager | None = None):
        self._auth = auth
        self._tools: dict[str, Tool] = {}

    def bind_auth(self, auth: AuthManager) -> None:
        self._auth = auth
        for tool in self._tools.values():
            tool.bind_auth(auth)

    def register(self, tool: Tool) -> Tool:
        if self._auth is not None:
            tool.bind_auth(self._auth)
        self._tools[tool.name] = tool
        return tool

    def unregister(self, name: str) -> None:
        self._tools.pop(name, None)

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def all(self) -> list[Tool]:
        return list(self._tools.values())

    def names(self) -> list[str]:
        return list(self._tools.keys())

    def schemas(self) -> list[dict[str, Any]]:
        return [t.to_openai_schema() for t in self._tools.values()]

    async def execute(self, name: str, role_level: int, ctx: ToolContext, **kwargs: Any) -> Any:
        tool = self._tools.get(name)
        if tool is None:
            raise ValueError(f"未知工具: {name}")
        decision = tool.check_permission(role_level)
        if decision == Decision.DENY:
            raise PermissionError(f"工具 {name} 需要更高权限(当前角色等级 {role_level})")
        if decision == Decision.ASK and role_level < 7:
            raise PermissionError(f"工具 {name} 需要管理员授权才能执行")
        return await tool.execute(ctx, **kwargs)
