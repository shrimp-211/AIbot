"""知识库管理器(compat:映射本项目 src/agent/knowledge 的 KnowledgeManager)。

dashboard 的 knowledge_base_service 通过本类访问本项目知识库:
- 单一知识库(kb_id="default")代表整个本项目知识库
- 文档/块直接读写本项目 KnowledgeManager 的内部数据
"""

from __future__ import annotations

import time
import uuid


class KnowledgeBaseManager:
    def __init__(self, project_km=None) -> None:
        self._km = project_km

    @property
    def km(self):
        return self._km

    # ---------------- 知识库 ----------------

    async def get_kbs(self, page: int = 1, page_size: int = 20) -> dict:
        stats = self._km.stats() if self._km is not None else {}
        kb = {
            "kb_id": "default",
            "kb_name": "默认知识库",
            "description": "本项目的 data/knowledge 向量知识库",
            "embedding_provider_id": None,
            "rerank_provider_id": None,
            "chunk_size": 800,
            "total_documents": stats.get("docs", 0),
            "total_chunks": stats.get("chunks", 0),
            "created_at": None,
        }
        return {
            "kbs": [kb],
            "pagination": {"page": page, "page_size": page_size, "total": 1, "total_pages": 1},
        }

    async def get_kb(self, kb_id: str | None = None) -> dict:
        stats = self._km.stats() if self._km is not None else {}
        return {
            "kb_id": "default",
            "kb_name": "默认知识库",
            "description": "本项目的 data/knowledge 向量知识库",
            "embedding_provider_id": None,
            "rerank_provider_id": None,
            "chunk_size": 800,
            "total_documents": stats.get("docs", 0),
            "total_chunks": stats.get("chunks", 0),
            "created_at": None,
        }

    async def create_kb(self, *args, **kwargs):
        return self._make_kb()

    async def update_kb(self, *args, **kwargs):
        return self._make_kb()

    async def delete_kb(self, *args, **kwargs):
        return None

    async def get_kb_stats(self, kb_id: str | None = None) -> dict:
        return self._km.stats() if self._km is not None else {}

    def _make_kb(self) -> dict:
        stats = self._km.stats() if self._km is not None else {}
        return {
            "kb_id": "default",
            "kb_name": "默认知识库",
            "description": "本项目的 data/knowledge 向量知识库",
            "total_documents": stats.get("docs", 0),
            "total_chunks": stats.get("chunks", 0),
        }

    # ---------------- 文档 ----------------

    def _docs(self) -> list[dict]:
        return list(getattr(self._km, "_docs", []) or [])

    def _chunks(self) -> dict:
        return dict(getattr(self._km, "_chunks", {}) or {})

    async def get_documents(
        self, page: int = 1, page_size: int = 20, search_query: str = ""
    ) -> dict:
        docs = self._docs()
        docs.sort(key=lambda d: d.get("created_at", 0), reverse=True)
        if search_query:
            q = search_query.lower()
            docs = [
                d for d in docs
                if q in (d.get("title", "") or "").lower()
                or q in (d.get("content", "") or "").lower()
            ]
        total = len(docs)
        start = (page - 1) * page_size
        items = docs[start : start + page_size]
        return {
            "documents": items,
            "pagination": {
                "page": page,
                "page_size": page_size,
                "total": total,
                "total_pages": (total + page_size - 1) // page_size if total else 0,
            },
        }

    async def get_document(self, doc_id: str) -> dict | None:
        for d in self._docs():
            if d.get("id") == doc_id:
                return d
        return None

    async def add_document(self, *, title, content, category="通用", source="", strategy="semantic", chunk_size=800) -> dict:
        if self._km is None:
            return {"ok": False, "error": "知识库管理器未配置"}
        return await self._km.add_document(
            title=title, content=content, category=category,
            source=source, strategy=strategy, chunk_size=chunk_size,
        )

    async def delete_document(self, doc_id: str) -> dict:
        if self._km is None:
            return {"ok": False, "error": "知识库管理器未配置"}
        return await self._km.delete_document(doc_id)

    async def list_chunks(self, doc_id: str, page: int = 1, page_size: int = 20) -> dict:
        chunks = [c for c in self._chunks().values() if c.get("doc_id") == doc_id]
        total = len(chunks)
        start = (page - 1) * page_size
        return {
            "chunks": chunks[start : start + page_size],
            "pagination": {"page": page, "page_size": page_size, "total": total, "total_pages": (total + page_size - 1) // page_size if total else 0},
        }

    async def delete_chunk(self, chunk_id: str) -> dict:
        return {"ok": True}

    async def retrieve(self, query: str, kb_id: str | None = None, top_k: int = 5) -> dict:
        if self._km is None:
            return {"count": 0, "results": []}
        return await self._km.search(query=query, limit=top_k)
