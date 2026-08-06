"""Cohere Rerank。对检索结果按 query 相关度重排。

config: {type: cohere, model: rerank-multilingual-v3.0, api_key}
"""
from __future__ import annotations

from typing import Any

import httpx

from ..base import RerankProvider


class CohereRerankProvider(RerankProvider):
    name = "cohere"

    async def rerank(self, query: str, documents: list[str], **kwargs: Any) -> list[float]:
        if not documents:
            return []
        api_key = self.config.get("api_key", "")
        if not api_key:
            # 无 Key 时退化:原样返回(调用方会跳过重排)
            return [1.0] * len(documents)
        url = "https://api.cohere.com/v2/rerank"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model or "rerank-multilingual-v3.0",
            "query": query,
            "documents": documents,
        }
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
        data = resp.json()
        results = data.get("results", [])
        if not results and isinstance(data, dict) and "data" in data:
            results = data["data"].get("results", [])
        scores = [0.0] * len(documents)
        for r in results:
            idx = r.get("index")
            if isinstance(idx, int) and 0 <= idx < len(documents):
                scores[idx] = float(r.get("relevance_score", 0.0))
        return scores
