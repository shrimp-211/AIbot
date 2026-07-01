"""三层记忆系统 — 工作记忆 + 短期记忆 + 长期记忆.

参考 mainidea.md 的三层记忆架构设计:
- 工作记忆 (Working Memory): 当前对话上下文，滑动窗口
- 短期记忆 (Episodic Memory): 近期会话摘要，JSON 文件持久化
- 长期记忆 (Semantic Memory): 用户画像，JSON 持久化
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from collections import defaultdict
from pathlib import Path
from typing import Any


class MemoryStore:
    """三层记忆存储.

    Attributes:
        working_size: 工作记忆容量 (最近 N 条消息)
        episodic_ttl: 短期记忆 TTL (秒)
    """

    def __init__(self, base_dir: str = "data", working_size: int = 20, episodic_ttl: int = 86400):
        self._base = Path(base_dir)
        self._base.mkdir(parents=True, exist_ok=True)

        # 工作记忆
        self._working: dict[str, list[dict]] = defaultdict(list)
        self.working_size = working_size

        # 短期记忆路径
        self._episodic_path = self._base / "episodic_memory.json"
        self._episodic: dict[str, list[dict]] = {}
        self.episodic_ttl = episodic_ttl

        # 长期记忆路径
        self._semantic_path = self._base / "semantic_memory.json"
        self._semantic: dict[str, dict] = {}

        # 攒批写入: 缓冲脏数据，定时刷新
        self._episodic_dirty = False
        self._semantic_dirty = False
        self._save_interval = 10  # 秒

        self._load()

    def _load(self) -> None:
        """从磁盘加载持久化记忆."""
        if self._episodic_path.exists():
            try:
                self._episodic = json.loads(self._episodic_path.read_text())
            except (json.JSONDecodeError, OSError):
                self._episodic = {}

        if self._semantic_path.exists():
            try:
                self._semantic = json.loads(self._semantic_path.read_text())
            except (json.JSONDecodeError, OSError):
                self._semantic = {}

    def _save_episodic(self) -> None:
        """标记短期记忆为脏，延迟批量写入."""
        self._episodic_dirty = True

    def _save_semantic(self) -> None:
        """标记长期记忆为脏，延迟批量写入."""
        self._semantic_dirty = True

    async def _flush_episodic(self) -> None:
        """实际写入短期记忆 (在后台任务中调用)."""
        if not self._episodic_dirty:
            return
        now = time.time()
        for sid in list(self._episodic.keys()):
            self._episodic[sid] = [
                e for e in self._episodic[sid]
                if now - e.get("timestamp", 0) < self.episodic_ttl
            ]
            if not self._episodic[sid]:
                del self._episodic[sid]
        data = json.dumps(self._episodic, ensure_ascii=False, indent=2)
        self._episodic_path.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(self._episodic_path.write_text, data)
        self._episodic_dirty = False

    async def _flush_semantic(self) -> None:
        """实际写入长期记忆."""
        if not self._semantic_dirty:
            return
        data = json.dumps(self._semantic, ensure_ascii=False, indent=2)
        self._semantic_path.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(self._semantic_path.write_text, data)
        self._semantic_dirty = False

    async def flush_all(self) -> None:
        """刷新所有脏数据到磁盘."""
        await asyncio.gather(
            self._flush_episodic(),
            self._flush_semantic(),
        )

    async def run_flush_loop(self, stop_event: asyncio.Event) -> None:
        """后台定期刷新任务."""
        while not stop_event.is_set():
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=self._save_interval)
            except asyncio.TimeoutError:
                await self.flush_all()

    # ---- 工作记忆 ----

    def add_working(self, session_id: str, role: str, content: str) -> None:
        """添加一条工作记忆."""
        self._working[session_id].append({"role": role, "content": content})
        # 滑动窗口
        if len(self._working[session_id]) > self.working_size:
            self._working[session_id] = self._working[session_id][-self.working_size:]

    def get_working(self, session_id: str) -> list[dict]:
        """获取工作记忆 (最近 N 条)."""
        return self._working.get(session_id, [])

    def get_working_count(self, session_id: str = "") -> int:
        """获取某会话的工作记忆条数."""
        if session_id:
            return len(self._working.get(session_id, []))
        return sum(len(v) for v in self._working.values())

    def clear_working(self, session_id: str) -> None:
        """清空某会话的工作记忆."""
        self._working.pop(session_id, None)

    # ---- 短期记忆 ----

    def add_episodic(self, session_id: str, role: str, content: str) -> None:
        """添加一条短期记忆."""
        if session_id not in self._episodic:
            self._episodic[session_id] = []
        self._episodic[session_id].append({
            "role": role,
            "content": content,
            "timestamp": time.time(),
        })
        # 限制每个会话的短期记忆数量
        if len(self._episodic[session_id]) > 100:
            self._episodic[session_id] = self._episodic[session_id][-100:]
        self._save_episodic()

    def get_episodic(self, session_id: str, limit: int = 10) -> list[dict]:
        """获取短期记忆."""
        entries = self._episodic.get(session_id, [])
        return entries[-limit:]

    # ---- 长期记忆 ----

    def set_user_profile(self, user_id: str, profile: str | dict) -> None:
        """设置用户画像."""
        if isinstance(profile, str):
            self._semantic[user_id] = {"profile": profile, "updated": time.time()}
        else:
            profile["updated"] = time.time()
            self._semantic[user_id] = profile
        self._save_semantic()

    def get_user_profile(self, user_id: str) -> str | None:
        """获取用户画像."""
        entry = self._semantic.get(user_id)
        if not entry:
            return None
        if isinstance(entry, dict):
            return entry.get("profile", str(entry))
        return str(entry)

    def update_user_profile(self, user_id: str, info: str) -> None:
        """追加用户信息到画像."""
        existing = self._semantic.get(user_id, {})
        if isinstance(existing, dict):
            existing["profile"] = (existing.get("profile", "") + "\n" + info).strip()
            existing["updated"] = time.time()
        else:
            existing = {"profile": info, "updated": time.time()}
        self._semantic[user_id] = existing
        self._save_semantic()
