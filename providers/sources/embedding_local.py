"""本地 Sentence-Transformers Embedding Provider(无需 API Key,离线可用)。

模型默认 paraphrase-multilingual-MiniLM-L12-v2(多语言,中文效果好,~470MB 首次下载)。
惰性加载:未安装 sentence-transformers 时不阻塞启动,调用期抛明确错误。
"""
from __future__ import annotations

import asyncio
from typing import Any

from loguru import logger

from ..base import EmbeddingProvider


class LocalEmbeddingProvider(EmbeddingProvider):
    name = "local"

    def __init__(self, config: dict[str, Any]):
        super().__init__(config)
        self.model_name = config.get("model", "paraphrase-multilingual-MiniLM-L12-v2")
        self._model = None

    def _get_model(self):
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as exc:
                raise RuntimeError(
                    "本地 Embedding 需要 sentence-transformers"
                    "(pip install sentence-transformers)"
                ) from exc
            logger.info("加载本地 Embedding 模型: {} ...", self.model_name)
            self._model = SentenceTransformer(self.model_name)
        return self._model

    async def embed(self, texts: list[str], **kwargs: Any) -> list[list[float]]:
        model = await asyncio.to_thread(self._get_model)

        def _run() -> list[list[float]]:
            vecs = model.encode(texts, normalize_embeddings=True, batch_size=16)
            return [list(map(float, v)) for v in vecs]

        return await asyncio.to_thread(_run)

    async def test(self) -> bool:
        try:
            model = await asyncio.to_thread(self._get_model)
            return model is not None
        except RuntimeError:
            return False
