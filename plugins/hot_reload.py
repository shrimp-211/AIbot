"""插件热重载:轮询目录文件变更,检测到新增/修改即重新加载插件。

通过 asyncio 后台任务轮询(简单可靠,无需 watchfiles 依赖)。
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from loguru import logger


class HotReloader:
    """目录文件变更监听 + 插件重载。"""

    def __init__(self, directories: list[str | Path], interval: float = 3.0):
        self.directories = [Path(d) for d in directories if Path(d).is_dir()]
        self.interval = max(0.5, float(interval))
        self._mtime: dict[str, float] = {}
        self._task: asyncio.Task | None = None
        self._reload_cb: Any = None

    def set_reload_cb(self, cb) -> None:
        """设置重载回调:async (changed_paths: list[str]) -> None。"""
        self._reload_cb = cb

    def _snapshot(self) -> dict[str, float]:
        snap: dict[str, float] = {}
        for d in self.directories:
            for p in d.rglob("*.py"):
                try:
                    snap[str(p)] = p.stat().st_mtime
                except OSError:
                    continue
        return snap

    async def _loop(self) -> None:
        self._mtime = self._snapshot()
        while True:
            await asyncio.sleep(self.interval)
            try:
                snap = self._snapshot()
                changed = [k for k, v in snap.items() if self._mtime.get(k) != v]
                if changed and self._reload_cb is not None:
                    logger.info("检测到插件文件变更: {}", changed)
                    try:
                        await self._reload_cb(changed)
                    except Exception:  # noqa: BLE001
                        logger.exception("插件热重载失败")
                self._mtime = snap
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                logger.exception("热重载轮询异常")

    def start(self) -> None:
        if self._task is None and self.directories:
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
