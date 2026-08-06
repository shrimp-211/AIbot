"""FAISS 稠密向量索引(惰性依赖,缺失时降级为纯关键词检索)。

使用 IndexIDMap2(支持 add_with_ids / remove),索引原子落盘。
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from loguru import logger


class VectorStore:
    """FAISS 索引封装。dim 在首次 add 时确定(惰性建索引)。

    faiss 的 add/search 在 C 层可能释放 GIL,异步事件循环内多个协程并发
    调用同一索引存在竞态风险,故用线程锁串行化全部索引操作。
    """

    def __init__(self, data_dir: str | Path):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.index_path = self.data_dir / "index.faiss"
        self._index: Any = None
        self._dim: int | None = None
        self._lock = threading.Lock()

    # ---------- 内部 ----------

    @property
    def has_index(self) -> bool:
        """是否有真正可用的索引。

        仅当 faiss 可导入且索引已加载/磁盘存在时才为 True;
        faiss 未安装时返回 False(让上层走纯关键词检索,而非搜索时崩溃)。
        """
        if self._index is not None:
            return True
        if not self.index_path.exists():
            return False
        try:
            self._load()
            return self._index is not None
        except RuntimeError:
            return False  # 未安装 faiss:磁盘文件存在但不可用

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

        with self._lock:
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
        try:
            idx = self._load()
        except RuntimeError:
            return []
        if idx is None:
            return []
        with self._lock:
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

        try:
            idx = self._load()
        except RuntimeError:
            return
        if idx is None:
            return
        with self._lock:
            idx.remove_ids(np.asarray([int(i) for i in ids], dtype="int64"))
            self._save()
