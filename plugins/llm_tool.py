"""@llm_tool 装饰器:把插件函数变成 LLM 可调用的结构化工具。

用法:
    from src.plugins.llm_tool import llm_tool

    @llm_tool(name="weather", description="查询天气", parameters={...})
    async def weather(ctx, city="北京", **kwargs):
        return {"city": city, "temp": "25°C"}

装饰器把函数包装成 PluginTool(兼容 AgentEngine 工具注册表),
注册到 PluginRegistry.llm_tools(),由 main.py 同步进引擎工具列表。
"""
from __future__ import annotations

import sys
from typing import Any, Callable

from ..agent.tools.base import Tool, ToolContext


class PluginTool(Tool):
    """包装插件函数的工具。execute 把 ToolContext 传给插件函数。"""

    def __init__(
        self,
        name: str,
        description: str,
        parameters: dict[str, Any],
        func: Callable[..., Any],
        permission_level: int = 0,
    ):
        super().__init__()
        self.name = name
        self.description = description or name
        self.parameters = parameters or {"type": "object", "properties": {}}
        self._func = func
        self.permission_level = permission_level

    async def execute(self, ctx: ToolContext, **kwargs: Any) -> Any:
        result = self._func(ctx, **kwargs)
        if hasattr(result, "__await__"):
            return await result
        return result


def llm_tool(
    name: str | None = None,
    description: str = "",
    parameters: dict[str, Any] | None = None,
    permission_level: int = 0,
):
    """装饰器:把 async 插件函数注册为 LLM 工具。"""

    def deco(func: Callable[..., Any]) -> Callable[..., Any]:
        tool = PluginTool(
            name or func.__name__,
            description or (func.__doc__ or "").strip() or func.__name__,
            parameters,
            func,
            permission_level=permission_level,
        )
        # 记录到模块级 __llm_tools__,由 registry._load_plugin 收集
        mod = sys.modules.get(func.__module__)
        if mod is not None:
            mod.__dict__.setdefault("__llm_tools__", []).append(tool)
        return func

    return deco
