"""极简 JSON 键值持久化 + 脏标记批量刷盘。

写盘走 `asyncio.to_thread()` + 原子替换(先写 .tmp 再 replace),避免阻塞事件循环。
"""
from __future__ import annotations

import asyncio
import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Any

from loguru import logger


class JsonKV:
    """线程安全的 JSON 键值存储,异步后台定时刷盘。"""

    def __init__(self, path: str | Path, flush_interval: float = 10.0):
        self._path = Path(path)
        self._data: dict[str, Any] = {}
        self._loaded = False
        self._dirty = False
        self._lock = threading.Lock()
        self._flush_interval = flush_interval
        self._task: asyncio.Task | None = None

    def _load_sync(self) -> None:
        """一次性加载磁盘数据。文件很小,仅执行一次。"""
        if self._loaded:
            return
        if self._path.exists():
            with open(self._path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
                if isinstance(loaded, dict):
                    self._data = loaded
        self._loaded = True

    def _ensure_loaded(self) -> None:
        # 未显式 initialize 时惰性回退(同步路径,如测试/同步调用方)
        if not self._loaded:
            self._load_sync()

    async def initialize(self) -> None:
        """异步加载磁盘数据(在 async 上下文调用,不阻塞事件循环)。"""
        if self._loaded:
            return
        await asyncio.to_thread(self._load_sync)

    async def _flush_loop(self) -> None:
        while True:
            await asyncio.sleep(self._flush_interval)
            try:
                await self.flush()
            except Exception:  # noqa: BLE001
                # 瞬时写错误(磁盘满/权限)不应杀死后台刷盘,下个周期重试
                logger.exception("JsonKV 后台刷盘失败,将在下个周期重试")

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
        try:
            await self.flush()
        except Exception:  # noqa: BLE001
            logger.exception("JsonKV 停止时刷盘失败")

    async def flush(self) -> None:
        """立即把脏数据写盘;写失败恢复脏标记,避免数据丢失。"""
        if not self._dirty:
            return
        with self._lock:
            data = dict(self._data)
            self._dirty = False
        try:
            await asyncio.to_thread(self._write, data)
        except Exception:
            # 写失败:恢复脏标记,下个周期(或调用方)重试,防止丢失数据
            self._dirty = True
            raise

    def _write(self, data: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(self._path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self._path)
        finally:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            self._ensure_loaded()
            return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            self._ensure_loaded()
            self._data[key] = value
            self._dirty = True

    def set_many(self, mapping: dict[str, Any]) -> None:
        with self._lock:
            self._ensure_loaded()
            self._data.update(mapping)
            self._dirty = True

    def delete(self, key: str) -> None:
        with self._lock:
            self._ensure_loaded()
            if key in self._data:
                del self._data[key]
                self._dirty = True

    def has(self, key: str) -> bool:
        with self._lock:
            self._ensure_loaded()
            return key in self._data

    def keys(self) -> list[str]:
        with self._lock:
            self._ensure_loaded()
            return list(self._data.keys())

    def get_all(self) -> dict[str, Any]:
        with self._lock:
            self._ensure_loaded()
            return dict(self._data)
