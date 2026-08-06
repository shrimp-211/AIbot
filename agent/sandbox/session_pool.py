"""沙箱会话资源池:按 session_id 复用沙箱组件,LRU 淘汰,防资源泄漏。

同一会话(用户/群)复用同一套沙箱(文件沙箱 root、shell workdir、浏览器 context),
池容量有限,超限按最近最少使用淘汰。
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any

from loguru import logger

from .file_sandbox import FileSandbox
from .python_sandbox import PythonSandbox
from .shell_sandbox import ShellSandbox


@dataclass
class SandboxSession:
    """单会话沙箱组件集合。"""

    session_id: str
    root: str
    shell: ShellSandbox = field(init=False)
    python: PythonSandbox = field(init=False)
    files: FileSandbox = field(init=False)
    browser: Any = field(default=None, init=False)
    last_used: float = field(default_factory=time.time)

    def __post_init__(self) -> None:
        self.shell = ShellSandbox(workdir=self.root)
        self.python = PythonSandbox(workdir=self.root)
        self.files = FileSandbox(root=self.root)

    async def close(self) -> None:
        if self.browser is not None:
            try:
                await self.browser.close()
            except Exception:  # noqa: BLE001
                pass
            self.browser = None


class SandboxSessionPool:
    """LRU 会话池。"""

    def __init__(self, max_sessions: int = 16, ttl: int = 600, base_dir: str = "data/sandbox"):
        self.max_sessions = max(1, int(max_sessions))
        self.ttl = int(ttl)
        self.base_dir = base_dir
        self._sessions: dict[str, SandboxSession] = {}
        self._lock = threading.Lock()

    def get(self, session_id: str) -> SandboxSession:
        key = str(session_id or "default")
        with self._lock:
            now = time.time()
            session = self._sessions.get(key)
            if session is not None:
                session.last_used = now
                return session
            self._evict_locked(now)
            root = f"{self.base_dir}/{_sanitize(key)}"
            session = SandboxSession(session_id=key, root=root)
            self._sessions[key] = session
            logger.debug("沙箱会话已创建: {} (池内 {} 个)", key, len(self._sessions))
            return session

    def _evict_locked(self, now: float) -> None:
        # 超 TTL 或超容量:淘汰最久未用
        stale = [k for k, s in self._sessions.items() if now - s.last_used > self.ttl]
        for k in stale:
            self._sessions.pop(k, None)
        while len(self._sessions) >= self.max_sessions:
            if not self._sessions:
                break
            lru = min(self._sessions, key=lambda k: self._sessions[k].last_used)
            self._sessions.pop(lru, None)

    def drop(self, session_id: str) -> None:
        with self._lock:
            self._sessions.pop(str(session_id or "default"), None)

    def size(self) -> int:
        return len(self._sessions)

    async def close_all(self) -> None:
        with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
        for s in sessions:
            try:
                await s.close()
            except Exception:  # noqa: BLE001
                pass


def _sanitize(key: str) -> str:
    import re

    return re.sub(r"[^A-Za-z0-9_\-]", "_", key)[:64] or "default"
