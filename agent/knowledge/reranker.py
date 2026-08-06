"""检索结果重排序(Cohere API 或本地 cross-encoder)。无可用重排器时返回原序。"""
from __future__ import annotations

from typing import Any


class Reranker:
    """包装上游 RerankProvider;缺失时重排退化为返回等分(不改变顺序)。"""

    def __init__(self, provider: Any = None):
        self.provider = provider

    @property
    def available(self) -> bool:
        return self.provider is not None

    async def rerank(self, query: str, documents: list[str]) -> list[float]:
        """按与 query 的相关度给 documents 打分(0~1,越大越相关)。"""
        if not documents:
            return []
        if self.provider is None:
            return [1.0] * len(documents)
        try:
            return await self.provider.rerank(query, documents)
        except Exception as exc:  # noqa: BLE001
            from loguru import logger

            logger.warning("重排失败,保持原序: {}", exc)
            return [1.0] * len(documents)
