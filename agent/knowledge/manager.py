"""知识库管理器:入库(分块+嵌入) → 混合检索(BM25 稀疏 + FAISS 稠密 + RRF 融合 + Rerank)。

- 依赖惰性:未装 faiss / 未配 embedding 时自动降级为纯关键词检索,不阻塞启动。
- 持久化:docs.json + chunks.json(JSON)+ index.faiss,全部原子替换写盘。
- 并发安全:asyncio.Lock 串行化写入。
"""
from __future__ import annotations

import asyncio
import json
import math
import uuid
from pathlib import Path
from typing import Any

from loguru import logger

from .bm25 import bm25_scores
from .chunking import chunk_text
from .reranker import Reranker
from .vector_store import VectorStore

_RRF_K = 60  # 融合常数


class KnowledgeManager:
    """向量知识库统一入口。"""

    def __init__(
        self,
        *,
        embedding_provider: Any = None,
        rerank_provider: Any = None,
        data_dir: str | Path = "data/knowledge",
    ):
        self.embedding = embedding_provider
        self.reranker = Reranker(rerank_provider)
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.docs_path = self.data_dir / "docs.json"
        self.chunks_path = self.data_dir / "chunks.json"
        self.vector = VectorStore(self.data_dir)

        self._docs: list[dict] = self._load_list(self.docs_path)
        self._chunks: dict[str, dict] = {}
        self._by_vec: dict[int, str] = {}
        self._doc_by_id: dict[str, dict] = {}
        self._vec_counter = 0
        for d in self._docs:
            self._doc_by_id[d["id"]] = d
        for c in self._load_list(self.chunks_path):
            self._chunks[c["id"]] = c
            self._by_vec[c["vec_id"]] = c["id"]
            self._vec_counter = max(self._vec_counter, int(c["vec_id"]) + 1)
        self._lock = asyncio.Lock()

    # ---------- 内部 ----------

    @staticmethod
    def _load_list(path: Path) -> list[dict]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except (OSError, ValueError):
            return []

    @staticmethod
    def _atomic_write(path: Path, obj: Any) -> None:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(path)

    def _persist(self) -> None:
        self._atomic_write(self.docs_path, self._docs)
        self._atomic_write(self.chunks_path, list(self._chunks.values()))

    @staticmethod
    def _l2_normalize(vectors: list[list[float]]) -> list[list[float]]:
        """归一化向量(配合 FAISS IndexFlatIP 做余弦相似度)。"""
        out = []
        for v in vectors:
            norm = math.sqrt(sum(x * x for x in v))
            out.append([x / norm for x in v] if norm else v)
        return out

    # ---------- 写入 ----------

    @property
    def vector_available(self) -> bool:
        return self.embedding is not None and self.vector.has_index

    def stats(self) -> dict:
        return {
            "docs": len(self._docs),
            "chunks": len(self._chunks),
            "vector_index": self.vector_available,
            "embedding": self.embedding is not None,
            "reranker": self.reranker.available,
            "index_vectors": self.vector.ntotal,
        }

    async def add_document(
        self,
        *,
        title: str,
        content: str,
        category: str = "通用",
        source: str = "",
        strategy: str = "semantic",
        chunk_size: int = 800,
    ) -> dict:
        """入库:分块 → 嵌入 → 索引 → 持久化。"""
        content = (content or "").strip()
        if not content:
            return {"ok": False, "error": "知识内容为空"}
        chunks = chunk_text(content, strategy=strategy, chunk_size=chunk_size) or [content]

        async with self._lock:
            doc_id = f"d_{uuid.uuid4().hex[:10]}"
            doc: dict[str, Any] = {
                "id": doc_id,
                "title": title or content[:30],
                "content": content,
                "category": category,
                "source": source,
                "created_at": int(time_ts()),
                "chunk_ids": [],
            }
            vec_ids: list[int] = []
            for t in chunks:
                cid = f"c_{uuid.uuid4().hex[:10]}"
                vec_id = self._vec_counter
                self._vec_counter += 1
                self._chunks[cid] = {
                    "id": cid,
                    "doc_id": doc_id,
                    "text": t,
                    "category": category,
                    "vec_id": vec_id,
                }
                self._by_vec[vec_id] = cid
                doc["chunk_ids"].append(cid)
                vec_ids.append(vec_id)
            self._docs.append(doc)
            self._doc_by_id[doc_id] = doc

            if self.embedding is not None:
                try:
                    vectors = await self.embedding.embed(chunks)
                    self.vector.add(self._l2_normalize(vectors), vec_ids)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("向量入库失败(保留文本,检索走关键词): {}", exc)

            await asyncio.to_thread(self._persist)
            return {
                "ok": True,
                "doc_id": doc_id,
                "chunks": len(chunks),
                "chars": len(content),
                "vector": self.embedding is not None,
            }

    async def delete_document(self, doc_id: str) -> dict:
        """删除文档及其块(含 FAISS 索引项)。"""
        async with self._lock:
            doc = self._doc_by_id.get(doc_id)
            if doc is None:
                return {"ok": False, "error": "文档不存在"}
            vec_ids = [
                self._chunks[cid]["vec_id"]
                for cid in doc.get("chunk_ids", [])
                if cid in self._chunks
            ]
            if vec_ids:
                try:
                    self.vector.remove(vec_ids)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("FAISS 删除失败: {}", exc)
            for cid in doc.get("chunk_ids", []):
                c = self._chunks.pop(cid, None)
                if c:
                    self._by_vec.pop(c["vec_id"], None)
            self._docs = [d for d in self._docs if d["id"] != doc_id]
            self._doc_by_id.pop(doc_id, None)
            await asyncio.to_thread(self._persist)
            return {"ok": True, "removed_chunks": len(vec_ids)}

    # ---------- 检索 ----------

    async def search(self, query: str, category: str | None = None, limit: int = 5) -> dict:
        """混合检索:BM25 + 向量 → RRF 融合 → Rerank 精排 → 汇总到文档。"""
        if not self._chunks:
            return {"count": 0, "results": [], "mode": "empty"}
        limit = max(1, int(limit or 5))
        chunks = self._candidate_chunks(category)

        bm25 = self._bm25(chunks, query)
        dense = await self._dense(chunks, query)
        fused = self._rrf(bm25, dense)
        if not fused:
            # 兜底:标题/关键词粗匹配
            fused = self._keyword_fallback(chunks, query)

        ordered = await self._rerank_and_order(query, chunks, fused)
        return self._aggregate_docs(ordered, limit, bool(dense))

    def _candidate_chunks(self, category: str | None) -> list[dict]:
        if not category:
            return list(self._chunks.values())
        return [c for c in self._chunks.values() if c.get("category") == category]

    def _bm25(self, chunks: list[dict], query: str) -> dict[str, float]:
        scores = bm25_scores(query, [c["text"] for c in chunks])
        return {c["id"]: s for c, s in zip(chunks, scores) if s > 0}

    async def _dense(self, chunks: list[dict], query: str) -> dict[str, float]:
        if self.embedding is None or not self.vector.has_index:
            return {}
        try:
            vec = (await self.embedding.embed([query]))[0]
        except Exception as exc:  # noqa: BLE001
            logger.warning("查询向量化失败: {}", exc)
            return {}
        out: dict[str, float] = {}
        for vec_id, score in self.vector.search(vec, k=max(50, len(chunks))):
            cid = self._by_vec.get(vec_id)
            if cid and cid in {c["id"] for c in chunks}:
                out[cid] = max(out.get(cid, 0.0), float(score))
        return out

    @staticmethod
    def _rrf(bm25: dict[str, float], dense: dict[str, float]) -> dict[str, float]:
        """Reciprocal Rank Fusion:按排名倒数加权,融合两路结果。"""
        merged: dict[str, float] = {}
        for scores in (bm25, dense):
            for rank, cid in enumerate(sorted(scores, key=scores.get, reverse=True)):
                merged[cid] = merged.get(cid, 0.0) + 1.0 / (_RRF_K + rank + 1)
        return merged

    @staticmethod
    def _keyword_fallback(chunks: list[dict], query: str) -> dict[str, float]:
        """无命中兜底:标题关键词粗匹配(旧行为兼容)。"""
        from .bm25 import char_tokens

        q = set(char_tokens(query))
        out: dict[str, float] = {}
        for c in chunks:
            hit = len(q & set(char_tokens(c["text"])))
            if hit:
                out[c["id"]] = float(hit)
        return out

    async def _rerank_and_order(
        self, query: str, chunks: list[dict], fused: dict[str, float]
    ) -> list[dict]:
        """对融合结果按 Rerank(若可用)或原融合分排序,返回有序 chunk 列表。"""
        cands = [c for c in chunks if c["id"] in fused]
        if not cands:
            return []
        if self.reranker.available:
            try:
                scores = await self.reranker.rerank(query, [c["text"] for c in cands])
                order = sorted(
                    range(len(cands)), key=lambda i: float(scores[i]), reverse=True
                )
                return [cands[i] for i in order]
            except Exception as exc:  # noqa: BLE001
                logger.warning("重排失败,按融合分排序: {}", exc)
        return sorted(cands, key=lambda c: fused[c["id"]], reverse=True)

    def _aggregate_docs(self, ordered: list[dict], limit: int, used_vector: bool) -> dict:
        results: list[dict] = []
        seen: set[str] = set()
        for c in ordered:
            doc = self._doc_by_id.get(c["doc_id"])
            if doc is None or c["doc_id"] in seen:
                continue
            seen.add(c["doc_id"])
            results.append(
                {
                    "doc_id": doc["id"],
                    "title": doc.get("title", ""),
                    "category": doc.get("category", ""),
                    "content": doc.get("content", "")[:2000],
                    "snippet": c["text"][:500],
                }
            )
            if len(results) >= limit:
                break
        return {"count": len(results), "results": results, "mode": "hybrid" if used_vector else "keyword"}


def time_ts() -> int:
    """可打桩的时间戳(便于测试)。"""
    import time

    return int(time.time())
