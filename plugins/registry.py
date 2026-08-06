"""插件注册中心:优先级分组 + 4 种 handler + 阻断 + 多轮会话控制。

handler 类型:
- command: 命令匹配(!/cmd 或直接 cmd)
- message: 匹配所有消息
- regex: 正则匹配
- llm: 预留给 LLM 钩子(当前不做路由)
"""
from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass
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
