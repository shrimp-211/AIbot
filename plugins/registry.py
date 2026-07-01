"""插件注册中心 — 参考 NoneBot2 PluginManager + Matcher 系统.

增强:
- 基于优先级的 Matcher 匹配 (数字越小越优先)
- Rule + Permission 组合检查
- require() 跨插件依赖
- 会话感知的事件分发
- block 阻断传播
"""

from __future__ import annotations

import importlib
import os
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger

from .base import BasePlugin, PluginMetadata, Rule, Permission
from .dependency import build_context, parse_dependent
from .session import (
    SessionFinished,
    SessionManager,
    SessionPaused,
    SessionRejected,
    SessionSkipped,
    SessionState,
)


@dataclass
class HandlerRegistration:
    """处理器注册信息."""
    func: callable
    priority: int = 1
    rule: Rule | None = None
    permission: Permission | None = None
    block: bool = False
    plugin_name: str = ""
    handler_type: str = "command"  # command | regex | on_message | on_llm_request | on_llm_response
    command: str = ""              # 命令名 (仅 command)
    regex: str = ""                # 正则 (仅 regex)
    source_module: str = ""        # 来源模块路径


class PluginRegistry:
    """增强的插件注册中心.

    特性:
    - 按优先级分组存储处理器
    - Rule + Permission 联合检查
    - 会话恢复 (SessionPaused → 继续执行)
    - require() 跨插件依赖声明
    - 插件信息查询
    """

    def __init__(self):
        self._plugins: dict[str, BasePlugin] = {}
        self._handlers: dict[int, list[HandlerRegistration]] = defaultdict(list)
        self._sessions = SessionManager()

        # 跨插件依赖跟踪
        self._loaded_module_names: set[str] = set()
        self._plugin_by_module: dict[str, str] = {}

    # ---- 插件注册 ----

    def register_plugin(self, plugin: BasePlugin) -> None:
        name = plugin.meta.name
        if name in self._plugins:
            logger.warning(f"插件 {name} 已注册，将被覆盖")
        self._plugins[name] = plugin
        logger.info(f"插件已加载: {name} v{plugin.meta.version}")

    def unregister_plugin(self, name: str) -> None:
        plugin = self._plugins.pop(name, None)
        if plugin:
            # 清理该插件的 handlers
            for prio in list(self._handlers.keys()):
                self._handlers[prio] = [
                    h for h in self._handlers[prio]
                    if h.plugin_name != name
                ]

    def get_plugin(self, name: str) -> BasePlugin | None:
        return self._plugins.get(name)

    def get_all_plugins(self) -> list[BasePlugin]:
        return list(self._plugins.values())

    def get_plugin_names(self) -> list[str]:
        return list(self._plugins.keys())

    # ---- Handler 注册 ----

    def register_handler(
        self,
        func: callable,
        handler_type: str = "command",
        priority: int = 1,
        rule: Rule | None = None,
        permission: Permission | None = None,
        block: bool = False,
        plugin_name: str = "",
        command: str = "",
        regex: str = "",
        source_module: str = "",
    ) -> HandlerRegistration:
        """注册一个事件处理器."""
        reg = HandlerRegistration(
            func=func,
            priority=priority,
            rule=rule,
            permission=permission,
            block=block,
            plugin_name=plugin_name,
            handler_type=handler_type,
            command=command,
            regex=regex,
            source_module=source_module,
        )
        self._handlers[priority].append(reg)
        logger.debug(f"Handler 注册: [{handler_type}] {command or regex} @ prio={priority}")
        return reg

    def command(
        self,
        cmd: str,
        priority: int = 1,
        rule: Rule | None = None,
        permission: Permission | None = None,
        block: bool = False,
    ):
        """装饰器: 注册命令处理器 (参考 NoneBot2 on_command)."""
        def decorator(func):
            self.register_handler(
                func=func,
                handler_type="command",
                command=cmd,
                priority=priority,
                rule=rule,
                permission=permission,
                block=block,
            )
            return func
        return decorator

    def on_message(
        self,
        priority: int = 1,
        rule: Rule | None = None,
        block: bool = False,
    ):
        """装饰器: 注册消息处理器."""
        def decorator(func):
            self.register_handler(
                func=func,
                handler_type="on_message",
                priority=priority,
                rule=rule,
                block=block,
            )
            return func
        return decorator

    def on_llm_request(self, priority: int = 1):
        """装饰器: 注册 LLM 请求前处理器."""
        def decorator(func):
            self.register_handler(
                func=func,
                handler_type="on_llm_request",
                priority=priority,
            )
            return func
        return decorator

    def on_llm_response(self, priority: int = 1):
        """装饰器: 注册 LLM 响应后处理器."""
        def decorator(func):
            self.register_handler(
                func=func,
                handler_type="on_llm_response",
                priority=priority,
            )
            return func
        return decorator

    # ---- 事件分发 ----

    async def dispatch(self, event) -> str | None:
        """分发事件到处理器.

        按优先级从小到大遍历，同优先级中顺序匹配。
        匹配逻辑: Permission → Rule → execute

        Returns:
            插件返回值 (None = 无匹配)
        """
        session_id = event.get_session_id()

        # 1. 检查是否有活跃会话 (会话恢复)
        active_session = self._sessions.get_active(session_id)
        if active_session and active_session.pending_got:
            return await self._resume_session(event, active_session)

        # 2. 按优先级匹配
        for priority in sorted(self._handlers.keys()):
            for handler in self._handlers[priority]:
                if not await self._match_handler(handler, event):
                    continue

                result = await self._execute_handler(handler, event)
                if handler.block:
                    return result
                if result is not None:
                    return result

        return None

    async def _match_handler(self, handler: HandlerRegistration, event) -> bool:
        """检查处理器是否匹配事件."""
        # Permission 检查
        if handler.permission:
            if not await handler.permission.check(event):
                return False

        # Rule 检查
        if handler.rule:
            if not await handler.rule.check(event):
                return False

        # 命令匹配
        if handler.handler_type == "command":
            text = event.text.strip()
            prefix = event.state.get("command_prefix", "/")
            if text.startswith(prefix + handler.command):
                # 提取命令参数
                args = text[len(prefix) + len(handler.command):].strip()
                event.state["command"] = handler.command
                event.state["command_arg"] = args
                return True
            return False

        # on_message: 匹配所有文本消息
        if handler.handler_type == "on_message":
            return bool(event.text.strip())

        # regex: 正则匹配
        if handler.handler_type == "regex":
            import re
            match = re.search(handler.regex, event.text)
            if match:
                event.state["regex_match"] = match
                event.state["regex_groups"] = match.groups()
                return True
            return False

        # LLM 钩子: 在对应管道阶段触发
        return False

    async def _execute_handler(self, handler: HandlerRegistration, event) -> str | None:
        """执行处理器 (含会话控制)."""
        plugin = self._plugins.get(handler.plugin_name)
        if not plugin:
            return None

        # 构建依赖注入上下文
        context = build_context(
            event=event,
            matcher=plugin,
            state=getattr(event, 'state', {}),
            message=event.text,
        )

        # 如果处理器需要依赖注入解析
        dep = parse_dependent(handler.func)

        try:
            result = await dep(**context)
            return str(result) if result is not None else None
        except SessionPaused as e:
            # 挂起会话: 注册到 SessionManager
            session_id = event.get_session_id()
            state = self._sessions.get_or_create(session_id)
            state.pending_got = plugin._state.pending_got or "__pause__"
            state.got_prompt = e.prompt
            if e.prompt:
                from .dependency import build_context
                event.reply(e.prompt)
            return None  # 不返回结果，等待用户回复
        except SessionRejected:
            return None
        except SessionFinished as e:
            session_id = event.get_session_id()
            self._sessions.clear(session_id)
            return e.message or None
        except SessionSkipped:
            return None
        except Exception:
            logger.exception(f"Handler 执行异常: {handler.plugin_name}/{handler.command}")
            return None

    async def _resume_session(self, event, state: SessionState) -> str | None:
        """恢复挂起的会话."""
        key = state.pending_got
        state.set(key, event.text)
        state.pending_got = None
        state.got_prompt = ""

        if state.remain_handlers:
            handler = state.remain_handlers[state.handler_index]
            context = build_context(event=event, state=state.data, message=event.text)
            dep = parse_dependent(handler)
            try:
                result = await dep(**context)
                return str(result) if result is not None else None
            except (SessionPaused, SessionRejected, SessionFinished, SessionSkipped):
                return None
            except Exception:
                logger.exception("Session resume failed")
                self._sessions.clear(event.get_session_id())
                return None

        return None

    # ---- 跨插件依赖 ----

    def require(self, plugin_name: str) -> BasePlugin:
        """声明跨插件依赖 — 参考 NoneBot2 require().

        如果插件已加载 → 返回
        如果插件未加载 → 尝试加载

        Args:
            plugin_name: 要依赖的插件名

        Returns:
            已加载的插件实例

        Raises:
            RuntimeError: 插件加载失败
        """
        if plugin_name in self._plugins:
            return self._plugins[plugin_name]

        # 尝试从 user_plugins 目录加载
        user_dir = Path("data/plugins")
        for item in user_dir.iterdir():
            if item.name == plugin_name:
                try:
                    self._load_plugin_from_dir(item)
                    if plugin_name in self._plugins:
                        return self._plugins[plugin_name]
                except Exception:
                    raise RuntimeError(f"无法加载依赖插件: {plugin_name}")

        raise RuntimeError(f"依赖插件未找到: {plugin_name}")

    def inherit_supported_adapters(self, *plugin_names: str) -> set[str] | None:
        """继承其他插件的适配器支持列表."""
        adapters = None
        for name in plugin_names:
            plugin = self._plugins.get(name)
            if plugin and plugin.meta.supported_adapters is not None:
                if adapters is None:
                    adapters = set()
                adapters.update(plugin.meta.supported_adapters)
        return adapters

    # ---- 插件加载 ----

    async def load_all(self) -> None:
        """加载所有插件."""
        builtin = Path(__file__).parent / "builtin"
        if builtin.exists():
            await self._load_from_dir(builtin)

        user_dir = Path("data/plugins")
        if user_dir.exists():
            await self._load_from_dir(user_dir)

    async def _load_from_dir(self, directory: Path) -> None:
        for item in directory.iterdir():
            if item.is_dir() and (item / "__init__.py").exists():
                await self._load_plugin_from_dir(item)

    async def _load_plugin_from_dir(self, directory: Path) -> None:
        try:
            module_name = str(directory.relative_to(Path.cwd())).replace(os.sep, ".")
            module = importlib.import_module(module_name)

            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                if (
                    isinstance(attr, type)
                    and issubclass(attr, BasePlugin)
                    and attr is not BasePlugin
                ):
                    plugin = attr()
                    await plugin.initialize()
                    self.register_plugin(plugin)
                    break
            else:
                logger.warning(f"目录中未找到 BasePlugin 子类: {directory}")
        except Exception:
            logger.exception(f"加载插件失败: {directory}")
