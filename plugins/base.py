"""插件基类 — 参考 NoneBot2 Plugin/PluginMetadata + Rule 组合.

增强:
- PluginMetadata 标准化 (name/description/usage/homepage/config/supported_adapters)
- Rule 组合 (& 运算符 for AND, | 运算符 for OR)
- 优先级 + block 阻断
- 会话 Mixin
"""

from __future__ import annotations

from abc import ABC
from dataclasses import dataclass, field
from typing import Any

from .session import SessionMixin, SessionState


@dataclass
class PluginMetadata:
    """插件元数据 — 参考 NoneBot2 PluginMetadata.

    Attributes:
        name: 插件名称 (人类可读)
        description: 插件介绍
        usage: 使用方式说明
        type: "application" (面向用户) 或 "library" (供其他插件使用)
        homepage: 项目主页
        config: 配置类 (可选)
        supported_adapters: 支持的适配器集合 (None 表示通用)
        extra: 额外信息
    """
    name: str
    description: str = ""
    usage: str = ""
    version: str = "1.0.0"
    author: str = ""
    type: str = "application"
    homepage: str = ""
    config: type | None = None
    supported_adapters: set[str] | None = None
    enabled: bool = True
    extra: dict[str, Any] = field(default_factory=dict)


class Rule:
    """响应规则 — RuleChecker 的 AND 集合.

    用法:
        rule = Rule(checker1, checker2)
        rule = to_me() & is_admin()
    """

    def __init__(self, *checkers):
        self._checkers = list(checkers)

    async def check(self, event, state: dict = None) -> bool:
        """所有 checker 通过才返回 True."""
        if not self._checkers:
            return True
        state = state or {}
        for checker in self._checkers:
            result = checker(event, state)
            if hasattr(result, '__await__'):
                import asyncio
                result = await result
            if not result:
                return False
        return True

    def __and__(self, other):
        """AND 组合规则."""
        if other is None:
            return self
        if isinstance(other, Rule):
            return Rule(*(self._checkers + other._checkers))
        return Rule(*(self._checkers + [other]))


class Permission:
    """权限检查 — PermissionChecker 的 OR 集合.

    任一 checker 通过即返回 True。
    """

    def __init__(self, *checkers):
        self._checkers = list(checkers)

    async def check(self, event, state: dict = None) -> bool:
        if not self._checkers:
            return True
        state = state or {}
        for checker in self._checkers:
            result = checker(event, state)
            if hasattr(result, '__await__'):
                import asyncio
                result = await result
            if result:
                return True
        return False

    def __or__(self, other):
        """OR 组合权限."""
        if other is None:
            return self
        if isinstance(other, Permission):
            return Permission(*(self._checkers + other._checkers))
        return Permission(*(self._checkers + [other]))


# ---- 内置 Rule Checker ----

def to_me():
    """创建"必须 @机器人"的规则."""
    def checker(event, state):
        return getattr(event, 'is_tome', False) or getattr(event, 'is_private', False)
    return Rule(checker)


def is_admin(auth=None):
    """创建"必须是管理员"的规则."""
    def checker(event, state):
        if auth is None:
            return True
        return auth.get_level(getattr(event, 'user_id', '')) >= 4
    return Rule(checker)


def command_startswith(prefix: str):
    """创建"必须以指定前缀开头"的规则."""
    def checker(event, state):
        text = getattr(event, 'text', '')
        return text.strip().startswith(prefix)
    return Rule(checker)


def is_group():
    """创建"必须是群聊消息"的规则."""
    def checker(event, state):
        return getattr(event, 'is_group', False)
    return Rule(checker)


def is_private():
    """创建"必须是私聊消息"的规则."""
    def checker(event, state):
        return getattr(event, 'is_private', False)
    return Rule(checker)


class BasePlugin(ABC, SessionMixin):
    """插件基类.

    使用示例:
        from plugins.base import BasePlugin, PluginMetadata, to_me
        from plugins.dependency import CommandArg

        class MyPlugin(BasePlugin):
            meta = PluginMetadata(
                name="我的插件", description="演示插件", usage="/hello",
            )

            def __init__(self):
                self._state = SessionState()

            @register_command("hello", rule=to_me())
            async def hello(self, event, arg: Message = CommandArg()):
                await self.finish(f"你好! 参数: {arg}")
    """

    meta: PluginMetadata = PluginMetadata(name="base")

    def __init__(self):
        self._state: SessionState = SessionState()

    async def initialize(self) -> None:
        """插件初始化."""
        pass

    async def terminate(self) -> None:
        """插件清理."""
        pass
