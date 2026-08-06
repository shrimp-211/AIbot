"""FAISS 稠密向量索引(惰性依赖,缺失时降级为纯关键词检索)。

使用 IndexIDMap2(支持 add_with_ids / remove),索引原子落盘。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from loguru import logger


class VectorStore:
    """FAISS 索引封装。dim 在首次 add 时确定(惰性建索引)。"""

    def __init__(self, data_dir: str | Path):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.index_path = self.data_dir / "index.faiss"
        self._index: Any = None
        self._dim: int | None = None

    # ---------- 内部 ----------

    @property
    def has_index(self) -> bool:
        """是否已有可用索引(内存已载 或 磁盘存在)。"""
        return self._index is not None or self.index_path.exists()

    @property
    def ntotal(self) -> int:
        """索引内向量数;faiss 缺失/未建索引时返回 0(适配无向量环境)。"""
        try:
            if self._index is None:
                self._load()
            return int(self._index.ntotal) if self._index is not None else 0
        except RuntimeError:
            return 0  # 未安装 faiss:统计降级为 0,不影响纯关键词模式

    def _load(self) -> Any:
        if self._index is not None:
            return self._index
        try:
            import faiss
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "向量检索需要 faiss-cpu(pip install faiss-cpu);未安装时自动回退关键词检索"
            ) from exc
        if self.index_path.exists():
            self._index = faiss.read_index(str(self.index_path))
            self._dim = self._index.d
        return self._index

    def _ensure(self, dim: int) -> Any:
        idx = self._load()
        if idx is None:
            try:
                import faiss
            except ImportError as exc:  # pragma: no cover
                raise RuntimeError("向量检索需要 faiss-cpu") from exc
            idx = faiss.IndexIDMap2(faiss.IndexFlatIP(dim))
            self._index = idx
            self._dim = dim
        return idx

    def _save(self) -> None:
        if self._index is None:
            return
        try:
            import faiss
        except ImportError:  # pragma: no cover
            return
        tmp = self.data_dir / "index.faiss.tmp"
        faiss.write_index(self._index, str(tmp))
        tmp.replace(self.index_path)  # 原子替换,防半写损坏

    # ---------- 公开 ----------

    def add(self, vectors: list[list[float]], ids: list[int]) -> None:
        """批量加入向量(归一化由上游保证)。"""
        if not vectors:
            return
        import numpy as np

        idx = self._ensure(len(vectors[0]))
        arr = np.asarray(vectors, dtype="float32")
        ids_arr = np.asarray([int(i) for i in ids], dtype="int64")
        idx.add_with_ids(arr, ids_arr)
        self._save()

    def search(self, vector: list[float], k: int = 20) -> list[tuple[int, float]]:
        """余弦/内积相似度 top-k,返回 [(vec_id, score)]。无索引返回 []。"""
        if not self.has_index:
            return []
        try:
            import numpy as np
        except ImportError:  # pragma: no cover
            return []
        idx = self._load()
        if idx is None:
            return []
        arr = np.asarray([vector], dtype="float32")
        scores, ids = idx.search(arr, max(1, int(k)))
        out: list[tuple[int, float]] = []
        for i, s in zip(ids[0], scores[0]):
            if i != -1:
                out.append((int(i), float(s)))
        return out

    def remove(self, ids: list[int]) -> None:
        """按 vec_id 删除(IndexIDMap2 支持 remove)。"""
        if not ids or not self.has_index:
            return
        import numpy as np

        idx = self._load()
        if idx is None:
            return
        idx.remove_ids(np.asarray([int(i) for i in ids], dtype="int64"))
        self._save()
