"""Provider 抽象基类(AstrBot 兼容,精简;本项目实际 LLM 由 src/providers 管理)。"""

from __future__ import annotations

import abc
from typing import Any


class AbstractProvider(abc.ABC):
    id: str = ""
    """the unique id of the provider instance that user configured"""
    type: str = ""
    """the name of the provider adapter, such as openai, ollama"""
    model: str | None = None
    """the model name of the provider instance currently used"""
    enable: bool = True


class Provider(AbstractProvider):
    """LLM(chat_completion)Provider 抽象。"""

    def __init__(self, *args, **kwargs) -> None:
        self.id = kwargs.get("id", "")
        self.type = kwargs.get("type", "")
        self.model = kwargs.get("model")
        self.enable = kwargs.get("enable", True)

    async def get_models(self) -> list[str]:
        return [self.model] if self.model else []

    async def chat(self, messages, system_prompt=None, tools=None, **kwargs) -> dict:
        return {"content": "", "tool_calls": [], "usage": None}

    async def test(self) -> tuple[bool, str]:
        return True, "ok"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "type": self.type,
            "model": self.model,
            "enable": self.enable,
        }


class STTProvider(AbstractProvider):
    pass


class TTSProvider(AbstractProvider):
    pass


class EmbeddingProvider(AbstractProvider):
    async def get_embedding(self, text: str) -> list[float]:
        return []


class RerankProvider(AbstractProvider):
    async def get_rerank(self, query: str, contexts: list[dict]) -> list[dict]:
        return contexts
