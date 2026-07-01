"""LLM 提供商层 — 参考 AstrBot Provider 模式统一不同模型的调用接口."""

from .base import BaseProvider
from .openai import OpenAIProvider
from .anthropic import AnthropicProvider


def create_provider(
    provider_type: str = "openai",
    model: str = "gpt-4o",
    api_key: str = "",
    base_url: str = "",
    **kwargs,
) -> BaseProvider:
    """工厂函数 — 根据类型创建对应的 Provider 实例."""
    if provider_type == "anthropic":
        return AnthropicProvider(model=model, api_key=api_key, base_url=base_url or "https://api.anthropic.com", **kwargs)
    # 默认 OpenAI 兼容
    return OpenAIProvider(model=model, api_key=api_key, base_url=base_url or "https://api.openai.com/v1", **kwargs)
