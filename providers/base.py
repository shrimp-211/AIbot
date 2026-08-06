"""LLM Provider 抽象 + 工厂函数。

统一 `chat()` 接口,返回 `{"content": str, "tool_calls": [...]}`。
tool_calls 元素格式:
    {"id": str, "name": str, "arguments": dict}
"""
from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from typing import Any

from loguru import logger

# 模型 -> 上下文窗口(按模型名子串匹配,越长 key 越优先)
_CONTEXT_WINDOWS: dict[str, int] = {
    "opus-4": 1_000_000,
    "sonnet-4": 1_000_000,
    "haiku-4": 200_000,
    "claude-3": 200_000,
    "claude": 200_000,
    "deepseek-r1": 163_840,
    "deepseek": 163_840,
    "gpt-4.1": 1_000_000,
    "gpt-4o": 128_000,
    "gpt-4": 128_000,
    "o1": 200_000,
    "o3": 200_000,
    "glm": 128_000,
    "qwen": 131_072,
    "kimi": 131_072,
    "moonshot": 131_072,
    "gemini": 1_000_000,
    "llama": 128_000,
    "mistral": 128_000,
}

_DEFAULT_CONTEXT_WINDOW = 128_000


def estimate_context_window(model: str) -> int:
    """按模型名推断上下文窗口(越具体的 key 越优先)。"""
    m = (model or "").lower()
    for key, win in sorted(_CONTEXT_WINDOWS.items(), key=lambda kv: -len(kv[0])):
        if key in m:
            return win
    return _DEFAULT_CONTEXT_WINDOW


class BaseProvider(ABC):
    name: str = "base"

    def __init__(self, config: dict[str, Any]):
        self.config = config
        self.model = config.get("model", "")
        self.base_url = config.get("base_url", "")
        self.api_key = config.get("api_key", "")
        self.max_tokens = int(config.get("max_tokens", 4096) or 4096)
        self.temperature = float(config.get("temperature", 0.7) or 0.7)
        # 上下文窗口:配置显式覆盖优先,否则按模型名推断
        configured = int(config.get("context_window", 0) or 0)
        self.context_window = configured or estimate_context_window(self.model)
        # 指数退避重试(网络抖动/限流/5xx 时自动重试)
        retry_cfg = config.get("retry") or {}
        self.max_attempts = max(
            1, int(retry_cfg.get("max_attempts", config.get("max_retries", 3)) or 3)
        )
        self.retry_base_delay = float(retry_cfg.get("base_delay", 0.5) or 0.5)
        self.retry_max_delay = float(retry_cfg.get("max_delay", 8.0) or 8.0)

    # ---------- 重试 ----------

    _RETRYABLE_CODES = frozenset((408, 429, 500, 502, 503, 504))

    @classmethod
    def _is_retryable(cls, exc: Exception) -> bool:
        """判断异常是否可重试:限流/超时/5xx/传输层错误。"""
        name = type(exc).__name__
        if name in (
            "RateLimitError",
            "APITimeoutError",
            "InternalServerError",
            "BadGatewayError",
            "ServiceUnavailableError",
            "APIConnectionError",
            "ConnectTimeout",
            "ReadTimeout",
            "TimeoutError",
        ):
            return True
        code = getattr(exc, "status_code", None)
        if code is not None:
            return code in cls._RETRYABLE_CODES
        # 传输层库异常兜底(httpx / aiohttp)
        mod = type(exc).__module__ or ""
        return "httpx" in mod or "aiohttp" in mod

    async def _with_retry(self, fn):
        """指数退避重试包装:base_delay * 2^i 递增,封顶 max_delay。"""
        last_exc: Exception | None = None
        for i in range(self.max_attempts):
            try:
                return await fn()
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                if not self._is_retryable(exc) or i == self.max_attempts - 1:
                    raise
                delay = min(self.retry_base_delay * (2**i), self.retry_max_delay)
                logger.warning(
                    "{} 调用失败({}),{:.1f}s 后重试({}/{})",
                    self.name,
                    type(exc).__name__,
                    delay,
                    i + 1,
                    self.max_attempts,
                )
                await asyncio.sleep(delay)
        assert last_exc is not None
        raise last_exc

    @abstractmethod
    async def chat(
        self,
        messages: list[dict],
        system_prompt: str | None = None,
        tools: list[dict] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """单次对话。

        Args:
            messages: OpenAI 格式消息列表
            system_prompt: 可选的 system prompt
            tools: OpenAI 格式工具定义列表(含 name/description/parameters)
        Returns:
            {"content": str, "tool_calls": [...], "raw": Any}
        """
        raise NotImplementedError

    @abstractmethod
    async def test(self) -> bool:
        """连通性测试。"""
        raise NotImplementedError


def create_provider(config: dict[str, Any]) -> BaseProvider:
    """按 type 创建 provider 实例。

    配置含 `fallback_providers` 时返回 ProviderManager(失败自动切换备用)。
    """
    if config.get("fallback_providers"):
        from .manager import ProviderManager

        return ProviderManager(config)
    ptype = (config.get("type") or "").lower()
    if ptype in ("anthropic", "claude"):
        from .anthropic import AnthropicProvider

        return AnthropicProvider(config)
    from .openai_compatible import OpenAICompatibleProvider

    return OpenAICompatibleProvider(config)
