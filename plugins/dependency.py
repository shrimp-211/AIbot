"""依赖注入系统 — 参考 NoneBot2 的 Depends/Param/Dependent 设计.

让插件处理器通过函数参数的类型注解声明式获取上下文:
  async def handler(event: AgentEvent, bot: Bot, state: dict, ...) -> str

支持的注入参数:
- AgentEvent / event: 当前事件对象
- Bot / bot: 当前 Bot 对象
- str / text: 事件纯文本
- MessageChain / message: 消息链
- dict / state: 会话状态字典
- Matcher / matcher: 当前 Matcher 实例
- CommandArg: 命令参数
- Depends: 子依赖函数
"""

from __future__ import annotations

import inspect
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from typing import Any, get_type_hints


@dataclass
class Dependent:
    """依赖容器 — 包装一个可调用对象及其解析后的参数列表."""

    func: Callable
    params: list["Param"] = field(default_factory=list)
    use_cache: bool = True

    async def __call__(self, **context) -> Any:
        """解析所有参数并调用原函数."""
        kwargs = {}
        for param in self.params:
            val = await param.resolve(context)
            kwargs[param.name] = val
        result = self.func(**kwargs)
        if inspect.iscoroutine(result):
            return await result
        return result


@dataclass
class Param:
    """单个参数的解析器.

    每个 Param 负责从 context 中提取对应类型的值。
    匹配优先级: 类型注解 > 参数名
    """

    name: str
    annotation: type | None = None
    default: Any = inspect.Parameter.empty

    async def resolve(self, context: dict) -> Any:
        """从上下文中解析此参数的值."""
        # 策略1: 按类型注解匹配 (排除 str，因为 str 太泛泛)
        if self.annotation is not None and self.annotation is not str:
            for val in context.values():
                if isinstance(val, self.annotation):
                    return val

        # 策略2: 按参数名匹配
        if self.name in context:
            return context[self.name]

        # 策略3: 默认值
        if self.default is not inspect.Parameter.empty:
            return self.default

        # 策略4: 无注解无默认 → 按名匹配失败 → 返回 None
        if self.annotation is None:
            return None

        # 策略5: str 类型回退到 message/text
        if self.annotation is str:
            return context.get("message", context.get("text", ""))

        raise ValueError(f"无法解析参数 '{self.name}' (类型: {self.annotation})")


# ---- 便捷注入器 (参考 NoneBot2 params.py) ----

class Depends:
    """子依赖 — 允许嵌套依赖解析.

    用法:
        async def get_db():
            return Database()

        async def handler(db = Depends(get_db)):
            ...

    Args:
        dependency: 子依赖函数
        use_cache: 是否在同一事件中缓存结果
    """

    def __init__(self, dependency: Callable, use_cache: bool = True):
        self.dependency = dependency
        self.use_cache = use_cache
        self._cache: Any | None = None

    async def resolve(self, context: dict) -> Any:
        if self.use_cache and self._cache is not None:
            return self._cache

        # 解析子依赖的参数
        dep = parse_dependent(self.dependency)
        result = await dep(**context)

        if self.use_cache:
            self._cache = result
        return result


class CommandArg:
    """命令参数注入器.

    用法:
        async def handler(arg = CommandArg()):
            await matcher.send(f"参数: {arg}")
    """
    pass


class EventText:
    """纯文本注入器."""
    pass


class Received:
    """receive 接收到的事件注入器."""
    pass


# ---- 依赖解析 ----

def parse_dependent(func: Callable) -> Dependent:
    """将函数解析为 Dependent 对象.

    分析函数的参数签名，为每个参数创建对应的 Param 解析器。
    """
    hints = {}
    try:
        hints = get_type_hints(func)
    except Exception:
        pass

    sig = inspect.signature(func)
    params = []

    for p_name, p_info in sig.parameters.items():
        annotation = hints.get(p_name)
        default = p_info.default if p_info.default is not inspect.Parameter.empty else inspect.Parameter.empty

        # 处理 Depends 默认值
        if isinstance(default, Depends):
            params.append(_DependsParam(name=p_name, depends=default))
            continue

        # 处理 CommandArg
        if annotation is CommandArg:
            params.append(_CommandArgParam(name=p_name))
            continue

        # 处理 EventText
        if annotation is EventText:
            params.append(_EventTextParam(name=p_name))
            continue

        params.append(Param(name=p_name, annotation=annotation, default=default))

    return Dependent(func=func, params=params)


# ---- 内部 Param 实现 ----

@dataclass
class _DependsParam:
    """Depends 子依赖参数."""
    name: str
    depends: Depends

    async def resolve(self, context: dict) -> Any:
        return await self.depends.resolve(context)


@dataclass
class _CommandArgParam:
    name: str

    async def resolve(self, context: dict) -> Any:
        state: dict = context.get("state", {})
        return state.get("command_arg", "")


@dataclass
class _EventTextParam:
    name: str

    async def resolve(self, context: dict) -> Any:
        event = context.get("event")
        if event and hasattr(event, "text"):
            return event.text
        return context.get("message", "")


# ---- 上下文构建 ----

def build_context(event=None, bot=None, matcher=None, state=None, message="") -> dict:
    """构建依赖解析上下文.

    将所有可用对象放入 context 字典供 Param 解析。
    """
    ctx = {}
    if event is not None:
        ctx["event"] = event
    if bot is not None:
        ctx["bot"] = bot
    if matcher is not None:
        ctx["matcher"] = matcher
    if state is not None:
        ctx["state"] = state
    if message:
        ctx["message"] = message
        ctx["text"] = message
    return ctx
