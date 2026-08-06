"""LLM Provider 抽象 + 工厂函数。

统一 `chat()` 接口,返回 `{"content": str, "tool_calls": [...]}`。
tool_calls 元素格式:
    {"id": str, "name": str, "arguments": dict}
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class BaseProvider(ABC):
    name: str = "base"

    def __init__(self, config: dict[str, Any]):
        self.config = config
        self.model = config.get("model", "")
        self.base_url = config.get("base_url", "")
        self.api_key = config.get("api_key", "")
        self.max_tokens = int(config.get("max_tokens", 4096) or 4096)
        self.temperature = float(config.get("temperature", 0.7) or 0.7)

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
    """按 type 创建 provider 实例。"""
    ptype = (config.get("type") or "").lower()
    if ptype in ("anthropic", "claude"):
        from .anthropic import AnthropicProvider

        return AnthropicProvider(config)
    from .openai_compatible import OpenAICompatibleProvider

    return OpenAICompatibleProvider(config)
