"""Provider 多实例 + fallback 自动切换(参照 AstrBot ProviderManager fallback)。

主 provider 调用失败(网络/鉴权/限流)时自动切换到备用 provider,保证服务可用性。
上层代码(engine/compressor)只需持有一个符合 BaseProvider 接口的对象,无需感知切换。
"""
from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from typing import Any

from loguru import logger

from .base import BaseProvider, create_provider


class ProviderManager(BaseProvider):
    """按 config.llm.provider 创建主 provider,fallback_providers 创建备用链。

    - 每次 `chat()` 从主 provider 开始尝试;失败后进入冷却,期间跳过,直到备用成功
    - 冷却到期后自动重试主 provider(瞬时故障自愈,持续故障不刷屏)
    - `__getattr__` 代理到最近一次成功调用的 provider,供上层只读访问
    """

    def __init__(
        self,
        config: dict[str, Any],
        *,
        factory: Callable[[dict[str, Any]], BaseProvider] = create_provider,
        cooldown_secs: float = 30.0,
    ):
        super().__init__(config)
        self._cooldown_secs = cooldown_secs
        main_cfg = {k: v for k, v in config.items() if k != "fallback_providers"}
        self._chain: list[BaseProvider] = [factory(main_cfg)]
        for fb in config.get("fallback_providers") or []:
            try:
                self._chain.append(factory(fb))
            except Exception:  # noqa: BLE001
                logger.exception("备用 provider 初始化失败: {}", fb)
        if not self._chain:
            raise ValueError("至少需要一个可用的 provider")
        self._active: BaseProvider = self._chain[0]
        self._cooldowns: dict[int, float] = {}
        self._lock = asyncio.Lock()

    async def chat(
        self,
        messages: list[dict],
        system_prompt: str | None = None,
        tools: list[dict] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        async with self._lock:
            now = time.monotonic()
            # 冷却中的 provider 跳过(全部冷却时强制全试,避免无候选)
            candidates = [
                i
                for i in range(len(self._chain))
                if self._cooldowns.get(i, 0) <= now or len(self._chain) <= 1
            ]
            if not candidates:
                candidates = list(range(len(self._chain)))

            last_exc: Exception | None = None
            for i in candidates:
                provider = self._chain[i]
                try:
                    result = await provider.chat(
                        messages, system_prompt=system_prompt, tools=tools, **kwargs
                    )
                    self._active = provider
                    self._cooldowns.pop(i, None)
                    return result
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    last_exc = exc
                    self._cooldowns[i] = now + self._cooldown_secs
                    logger.warning(
                        "Provider {} 调用失败,{}s 内不再尝试: {}",
                        type(provider).__name__,
                        self._cooldown_secs,
                        exc,
                    )
            if last_exc is not None:
                raise last_exc
            raise RuntimeError("无可用 LLM provider")

    async def test(self) -> bool:
        for provider in self._chain:
            try:
                if await provider.test():
                    return True
            except Exception:  # noqa: BLE001
                continue
        return False

    @property
    def active(self) -> BaseProvider:
        """最近一次成功调用的 provider(无调用时为主 provider)。"""
        return self._active

    def __getattr__(self, name: str) -> Any:
        active = self.__dict__.get("_active")
        if active is not None:
            return getattr(active, name)
        raise AttributeError(name)
