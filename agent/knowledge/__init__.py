"""向量知识库包:分块 → 嵌入 → 混合检索(BM25 + 向量 + RRF) → 重排。"""
from .chunking import chunk_text
from .manager import KnowledgeManager

__all__ = ["KnowledgeManager", "chunk_text"]
