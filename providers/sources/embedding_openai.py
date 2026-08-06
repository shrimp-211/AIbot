"""OpenAI 兼容 Embedding。适用于 OpenAI / DeepSeek / 硅基流动等 OpenAI 兼容服务。

config: {type: openai, model: text-embedding-3-small, api_key, base_url}
"""
from __future__ import annotations

from typing import Any

import httpx

from ..base import EmbeddingProvider


class OpenAIEmbeddingProvider(EmbeddingProvider):
    name = "openai_embedding"

    async def embed(self, texts: list[str], **kwargs: Any) -> list[list[float]]:
        if not texts:
            return []
        api_key = self.config.get("api_key", "")
        if not api_key:
            raise RuntimeError(
                "Embedding API 未配置 api_key(可在 config.yaml 的 provider_embedding 段设置)"
            )
        base_url = (self.config.get("base_url") or "https://api.openai.com/v1").rstrip("/")
        url = f"{base_url}/embeddings"
        headers = {"Authorization": f"Bearer {api_key}"}
        payload = {"model": self.model or "text-embedding-3-small", "input": texts}
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
        data = resp.json()["data"]
        # 按索引排序,避免服务端乱序
        data.sort(key=lambda x: x.get("index", 0))
        return [item["embedding"] for item in data]
