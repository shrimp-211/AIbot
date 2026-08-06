"""审计日志:记录工具调用与权限决策,持久化到 data/audit.jsonl。

写入采用追加模式,线程锁保护 + `asyncio.to_thread` 避免阻塞事件循环。
"""
from __future__ import annotations

import asyncio
import json
import threading
import time
from pathlib import Path
from typing import Any


class AuditLogger:
    def __init__(self, path: str | Path, enabled: bool = True):
        self._path = Path(path)
        self._enabled = enabled
        self._lock = threading.Lock()

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled

    async def log(
        self,
        *,
        user_id: str = "",
        group_id: str | None = None,
        action: str = "",
        tool_name: str | None = None,
        detail: str = "",
        decision: str = "",
        reason: str = "",
        status: str = "ok",
    ) -> None:
        """写一条审计记录。"""
        if not self._enabled:
            return
        record: dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
            "user_id": user_id,
            "group_id": group_id,
            "action": action,
            "tool_name": tool_name,
            "detail": detail[:2000],
            "decision": decision,
            "reason": reason,
            "status": status,
        }
        await asyncio.to_thread(self._write_sync, record)

    def _write_sync(self, record: dict[str, Any]) -> None:
        try:
            with self._lock:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                with open(self._path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError:
            pass
