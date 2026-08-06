"""极简 JSON 键值持久化 + 脏标记批量刷盘。

写盘走 `asyncio.to_thread()` + 原子替换(先写 .tmp 再 replace),避免阻塞事件循环。
"""
from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path
from typing import Any


class JsonKV:
    """线程安全的 JSON 键值存储,异步后台定时刷盘。"""

    def __init__(self, path: str | Path, flush_interval: float = 10.0):
        self._path = Path(path)
        self._data: dict[str, Any] = {}
        self._dirty = False
        self._lock = threading.Lock()
        self._flush_interval = flush_interval
        self._task: asyncio.Task | None = None
        self._load()

    def _load(self) -> None:
        if self._path.exists():
            with open(self._path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
                if isinstance(loaded, dict):
                    self._data = loaded

    async def _flush_loop(self) -> None:
        while True:
            await asyncio.sleep(self._flush_interval)
            await self.flush()

    def start(self) -> None:
        """启动后台刷盘循环。"""
        if self._task is None:
            self._task = asyncio.get_running_loop().create_task(self._flush_loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        await self.flush()

    async def flush(self) -> None:
        """立即把脏数据写盘。"""
        if not self._dirty:
            return
        with self._lock:
            data = dict(self._data)
            self._dirty = False
        await asyncio.to_thread(self._write, data)

    def _write(self, data: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        tmp.replace(self._path)

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            self._data[key] = value
            self._dirty = True

    def set_many(self, mapping: dict[str, Any]) -> None:
        with self._lock:
            self._data.update(mapping)
            self._dirty = True

    def delete(self, key: str) -> None:
        with self._lock:
            if key in self._data:
                del self._data[key]
                self._dirty = True

    def has(self, key: str) -> bool:
        with self._lock:
            return key in self._data

    def keys(self) -> list[str]:
        with self._lock:
            return list(self._data.keys())

    def get_all(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._data)
