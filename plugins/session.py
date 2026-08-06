"""多轮会话控制(参考 NoneBot2 的异常流控)。

got(key, prompt) 获取参数;pause/reject/finish/skip 通过异常实现控制流,
避免真正挂起协程。会话状态按 session_id 保存,超时自动清理。
"""
from __future__ import annotations

import time
from functools import wraps
from typing import Any, Awaitable, Callable

from ..adapter.event import AgentEvent


class Paused(Exception):
    """暂停等待下一条消息,不重新执行当前 handler。"""

    def __init__(self, prompt: str | None = None):
        self.prompt = prompt


class Rejected(Exception):
    """拒绝当前输入,终止会话。"""

    def __init__(self, prompt: str | None = None):
        self.prompt = prompt


class Finished(Exception):
    """结束会话。"""


class Skipped(Exception):
    """跳过当前 handler。"""


class SessionControl:
    def __init__(self, ttl: int = 600):
        self._ttl = ttl
        # session_id -> {func, awaiting_key, data, expire}
        self._pending: dict[str, dict[str, Any]] = {}

    # ---------- 会话分发 ----------

    async def dispatch(self, event: AgentEvent) -> bool:
        """若有待恢复的多轮会话,消费当前消息并继续执行。"""
        sid = event.session_id
        pend = self._pending.get(sid)
        if not pend:
            return False
        if pend.get("expire", 0) < time.time():
            self._pending.pop(sid, None)
            return False

        data = pend["data"]
        data[pend["awaiting_key"]] = event.plain_text
        func = pend["func"]
        self._pending.pop(sid, None)
        try:
            result = await func(event, data)
        except Paused as exc:
            self._pending[sid] = {
                "func": func,
                "awaiting_key": pend["awaiting_key"],
                "data": data,
                "expire": time.time() + self._ttl,
            }
            await event.reply(exc.prompt or "请继续。")
            return True
        except Rejected as exc:
            await event.reply(exc.prompt or "输入无效,会话已结束。")
            return True
        except Finished:
            return True
        except Exception:  # noqa: BLE001
            self._pending.pop(sid, None)
            raise
        if result:
            await event.reply(str(result))
        return True

    # ---------- 公开 API ----------

    def schedule(
        self,
        sid: str,
        func: Callable[..., Awaitable[Any]],
        awaiting_key: str,
        data: dict[str, Any],
        ttl: int | None = None,
    ) -> None:
        """登记一个待恢复的多轮会话:下次同会话消息触发 dispatch 继续执行。"""
        self._pending[sid] = {
            "func": func,
            "awaiting_key": awaiting_key,
            "data": data,
            "expire": time.time() + (ttl if ttl is not None else self._ttl),
        }

    # ---------- 装饰器 ----------

    def got(self, key: str, prompt: str | None = None):
        """获取用户参数。缺失时发送 prompt 并挂起等待回复。

        handler 签名: `async def func(event: AgentEvent, data: dict)`。
        """

        def deco(func: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
            @wraps(func)
            async def wrapper(event: AgentEvent, data: dict[str, Any] | None = None) -> Any:
                data = data or {}
                if key not in data:
                    await event.reply(prompt or f"请输入 {key}:")
                    sid = event.session_id
                    self._pending[sid] = {
                        "func": wrapper,
                        "awaiting_key": key,
                        "data": data,
                        "expire": time.time() + self._ttl,
                    }
                    raise Finished
                return await func(event, data)

            return wrapper

        return deco

    def pending_count(self) -> int:
        return len(self._pending)
