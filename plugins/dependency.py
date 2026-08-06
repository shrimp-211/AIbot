"""依赖注入(参考 NoneBot2 设计,简化版):按类型注解解析 handler 参数。

支持注入:AgentEvent、MessageChain、Config、AuthManager、ToolRegistry、
MemoryStore 以及任意注册的依赖类型;也可用 `Depends(func)` 显式声明。
"""
from __future__ import annotations

import inspect
from typing import Any, Callable

from ..adapter.event import AgentEvent
from ..adapter.message import MessageChain


class _DependsInner:
    def __init__(self, dependency: Callable[..., Any] | None = None):
        self.dependency = dependency


def Depends(dependency: Callable[..., Any] | None = None) -> Any:
    """声明式依赖,`arg: Any = Depends(func)`。"""
    return _DependsInner(dependency)


async def resolve_params(func: Callable[..., Any], event: AgentEvent, deps: dict[type, Any]) -> dict[str, Any]:
    """根据函数签名和类型注解注入参数。"""
    sig = inspect.signature(func)
    params: dict[str, Any] = {}
    for name, param in sig.parameters.items():
        ann = param.annotation

        if ann in deps:
            params[name] = deps[ann]
        elif ann is AgentEvent:
            params[name] = event
        elif ann is MessageChain:
            params[name] = event.message
        elif isinstance(param.default, _DependsInner):
            dep = param.default.dependency
            if dep is None:
                params[name] = None
            else:
                result = dep(**params)
                params[name] = await result if inspect.isawaitable(result) else result
        elif param.default is not inspect.Parameter.empty:
            params[name] = param.default
        elif name in ("event",):
            params[name] = event
        elif name in ("message", "chain"):
            params[name] = event.message
        elif name in ("text", "msg", "content"):
            params[name] = event.plain_text
        else:
            params[name] = event.plain_text
    return params
