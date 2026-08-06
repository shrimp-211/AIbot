"""插件注册中心:优先级分组 + 4 种 handler + 阻断 + 多轮会话控制。

handler 类型:
- command: 命令匹配(!/cmd 或直接 cmd)
- message: 匹配所有消息
- regex: 正则匹配
- llm: 自然语言意图匹配(关键词命中)

外部插件:data/plugins/ 下的 .py 文件通过 importlib 加载,
支持 `setup(registry)` 入口函数,可热重载(reload 前自动卸载)。
"""
from __future__ import annotations

import asyncio
import importlib.util
import inspect
import logging
import re
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from ..adapter.event import AgentEvent
from ..security.auth import AuthManager
from .dependency import resolve_params
from .session import Finished, Paused, Rejected, SessionControl, Skipped

_logger = logging.getLogger("plugins")


@dataclass
class PluginHandler:
    id: str
    handler: Callable[..., Awaitable[Any]]
    matcher_type: str  # command | message | regex | llm
    pattern: str = ""
    description: str = ""  # llm 类型:自然语言意图描述
    priority: int = 10
    block: bool = False
    permission_level: int = 0
    keywords: list[str] | None = None  # llm 类型:由描述提取的关键词

    def match(self, event: AgentEvent) -> bool:
        text = event.plain_text.strip()
        if self.matcher_type == "message":
            return True
        if self.matcher_type == "command":
            arg = self._match_command(text)
            if arg is not None:
                event.state["command_arg"] = arg
                return True
            return False
        if self.matcher_type == "regex":
            m = re.search(self.pattern, text)
            if m:
                event.state["regex_match"] = m
                event.state["regex_text"] = text
                return True
            return False
        if self.matcher_type == "llm":
            if not self.keywords:
                return False
            # 命中任一描述关键词即视为意图匹配
            return any(kw in text for kw in self.keywords)
        return False

    def _match_command(self, text: str) -> str | None:
        if text == self.pattern:
            return ""
        if text.startswith(self.pattern + " "):
            return text[len(self.pattern) :].strip()
        for prefix in ("!", "/"):
            full = prefix + self.pattern
            if text == full:
                return ""
            if text.startswith(full + " "):
                return text[len(full) :].strip()
        return None


def _extract_keywords(text: str, max_kw: int = 10) -> list[str]:
    """从描述中提取匹配关键词:中文整词 + 2 字滑动窗口;英文 3+ 字母词。"""
    kws: list[str] = []
    for w in re.split(r"[\s,，。;；:：()（）]+", text):
        w = w.strip().lower()
        if not w:
            continue
        if "一" <= w[0] <= "鿿":
            if len(w) >= 2:
                kws.append(w)
            if len(w) >= 3:
                kws.extend(w[i : i + 2] for i in range(len(w) - 1))
        elif len(w) >= 3:
            kws.append(w)
        if len(kws) >= max_kw:
            break
    return list(dict.fromkeys(kws))


class PluginRegistry:
    def __init__(self, auth: AuthManager | None = None, deps: dict[type, Any] | None = None):
        self._auth = auth
        self._handlers: list[PluginHandler] = []
        self.sessions = SessionControl()
        self._deps: dict[type, Any] = dict(deps or {})
        self._external_ids: list[str] = []  # 外部插件注册的 handler id,供卸载/重载

    def register_dependency(self, type_: type, value: Any) -> None:
        self._deps[type_] = value

    def register(self, handler: PluginHandler) -> None:
        self._handlers.append(handler)
        self._handlers.sort(key=lambda h: h.priority)

    # ---------- 装饰器 ----------

    def command(self, name: str, priority: int = 10, block: bool = True, permission_level: int = 0):
        def deco(func):
            self.register(
                PluginHandler(
                    id=uuid.uuid4().hex[:8],
                    handler=func,
                    matcher_type="command",
                    pattern=name,
                    priority=priority,
                    block=block,
                    permission_level=permission_level,
                )
            )
            return func

        return deco

    def message(self, priority: int = 100, block: bool = False, permission_level: int = 0):
        def deco(func):
            self.register(
                PluginHandler(
                    id=uuid.uuid4().hex[:8],
                    handler=func,
                    matcher_type="message",
                    priority=priority,
                    block=block,
                    permission_level=permission_level,
                )
            )
            return func

        return deco

    def regex(self, pattern: str, priority: int = 10, block: bool = True, permission_level: int = 0):
        def deco(func):
            self.register(
                PluginHandler(
                    id=uuid.uuid4().hex[:8],
                    handler=func,
                    matcher_type="regex",
                    pattern=pattern,
                    priority=priority,
                    block=block,
                    permission_level=permission_level,
                )
            )
            return func

        return deco

    def llm(self, description: str, priority: int = 50, block: bool = True, permission_level: int = 0):
        """自然语言意图插件:description 描述该插件处理的请求类型。

        匹配规则:消息文本命中描述中 >=2 个关键词时触发。
        """
        keywords = _extract_keywords(description)

        def deco(func):
            self.register(
                PluginHandler(
                    id=uuid.uuid4().hex[:8],
                    handler=func,
                    matcher_type="llm",
                    description=description,
                    priority=priority,
                    block=block,
                    permission_level=permission_level,
                    keywords=keywords,
                )
            )
            return func

        return deco

    def got(self, key: str, prompt: str | None = None):
        """多轮会话:获取用户参数。"""
        return self.sessions.got(key, prompt)

    # ---------- 分发 ----------

    async def dispatch(self, event: AgentEvent) -> bool:
        """尝试分发事件。返回 True 表示已被插件处理(阻断后续)。"""
        if await self.sessions.dispatch(event):
            return True

        role_level = (
            self._auth.get_role_level(event.user_id, event.group_id) if self._auth else 7
        )
        for h in self._handlers:
            if not h.match(event):
                continue
            if role_level < h.permission_level:
                continue
            try:
                params = await resolve_params(h.handler, event, self._deps)
                result = await h.handler(**params)
            except Skipped:
                continue
            except Paused as exc:
                await event.reply(exc.prompt or "请继续。")
                return True
            except Rejected as exc:
                await event.reply(exc.prompt or "输入无效,会话已结束。")
                return True
            except Finished:
                return True
            except Exception:  # noqa: BLE001
                _logger.exception("插件 handler 执行异常: %s", h.id)
                continue
            if h.block or result is True:
                return True
        return False

    def handler_count(self) -> int:
        return len(self._handlers)

    # ---------- 外部插件加载 ----------

    async def load_from_directory(self, directory: str | Path) -> list[str]:
        """从目录加载外部 .py 插件,返回成功加载的插件名列表。

        插件文件可直接用 @registry.command() 等装饰器注册 handler,
        也可定义 `setup(registry)` / `register(registry)` 入口函数
        (支持 async,可接收依赖注入参数)。
        """
        directory = Path(directory)
        if not directory.is_dir():
            return []
        loaded: list[str] = []
        for path in sorted(directory.glob("*.py")):
            if path.name.startswith("_"):
                continue
            try:
                await self._load_plugin(path)
                loaded.append(path.stem)
            except Exception:  # noqa: BLE001
                _logger.exception("外部插件加载失败: %s", path.name)
        return loaded

    async def _load_plugin(self, path: Path) -> None:
        module_name = f"qqbot_external_{path.stem}"
        sys.modules.pop(module_name, None)  # 清理缓存以支持热重载
        before = {h.id for h in self._handlers}
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            raise ImportError(f"无法解析插件模块: {path.name}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        setup = getattr(module, "setup", None) or getattr(module, "register", None)
        if callable(setup):
            result = setup(self)
            if inspect.isawaitable(result):
                await result
        new_ids = [h.id for h in self._handlers if h.id not in before]
        self._external_ids.extend(new_ids)
        _logger.info("外部插件已加载: %s (%d 个 handler)", path.stem, len(new_ids))

    def unload_external(self) -> None:
        """卸载所有外部插件注册的 handler 并清理模块缓存。"""
        ids = set(self._external_ids)
        if ids:
            self._handlers = [h for h in self._handlers if h.id not in ids]
            self._handlers.sort(key=lambda h: h.priority)
            self._external_ids.clear()
        for mod in list(sys.modules):
            if mod.startswith("qqbot_external_"):
                sys.modules.pop(mod, None)

    async def reload_from_directory(self, directory: str | Path) -> list[str]:
        """先卸载全部外部插件,再重新加载。返回加载的插件名。"""
        self.unload_external()
        return await self.load_from_directory(directory)
