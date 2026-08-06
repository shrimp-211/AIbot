"""三层记忆:工作记忆(滑动窗口) + 短期记忆(TTL) + 长期记忆(用户画像)。

工作记忆驻留内存;短期记忆与长期记忆由 JsonKV 持久化。
"""
from __future__ import annotations

import time
from typing import Any


class MemoryStore:
    def __init__(
        self,
        db: Any,
        window_size: int = 20,
        episodic_ttl: int = 86400,
    ):
        self._db = db
        self._window_size = window_size
        self._episodic_ttl = episodic_ttl
        # session_id -> deque of {role, content, ts}
        self._working: dict[str, list[dict]] = {}

    # ---------- 工作记忆 ----------

    def add_message(self, session_id: str, role: str, content: str) -> None:
        buf = self._working.setdefault(session_id, [])
        buf.append({"role": role, "content": content, "ts": time.time()})
        if len(buf) > self._window_size:
            overflow = buf[: -self._window_size]
            self._append_episodic(session_id, overflow)
            self._working[session_id] = buf[-self._window_size :]

    def get_working(self, session_id: str) -> list[dict]:
        return list(self._working.get(session_id, []))

    def clear_working(self, session_id: str) -> None:
        self._working.pop(session_id, None)

    # ---------- 短期记忆(Episodic) ----------

    def _append_episodic(self, session_id: str, entries: list[dict]) -> None:
        all_episodes = self._db.get("episodic", {})
        episodes = all_episodes.setdefault(session_id, [])
        episodes.extend(entries)
        # 裁剪 + 清理过期
        now = time.time()
        episodes = [e for e in episodes if now - e.get("ts", 0) < self._episodic_ttl]
        all_episodes[session_id] = episodes[-100:]
        self._db.set("episodic", all_episodes)

    def get_episodic(self, session_id: str, limit: int = 10) -> list[dict]:
        all_episodes = self._db.get("episodic", {})
        episodes = all_episodes.get(session_id, [])
        now = time.time()
        episodes = [e for e in episodes if now - e.get("ts", 0) < self._episodic_ttl]
        return episodes[-limit:]

    # ---------- 长期记忆(用户画像) ----------

    def update_profile(self, user_id: str, key: str, value: Any) -> None:
        profiles = self._db.get("user_profiles", {})
        profile = profiles.setdefault(user_id, {})
        profile[key] = value
        profile["_updated_at"] = int(time.time())
        profiles[user_id] = profile
        self._db.set("user_profiles", profiles)

    def get_profile(self, user_id: str) -> dict[str, Any]:
        return dict(self._db.get("user_profiles", {}).get(user_id, {}))

    def all_profiles(self) -> dict[str, Any]:
        return self._db.get("user_profiles", {})

    # ---------- 自动记忆(LLM 提取的关键信息) ----------

    def save_auto_memory(self, user_id: str, memory_type: str, content: str) -> None:
        """保存自动提取的关键信息,按类型(user_pref/fact/project/debug)分类。"""
        store = self._db.get("auto_memory", {})
        bucket = store.setdefault(memory_type, {})
        items = bucket.setdefault(user_id, [])
        if len(items) > 200:
            items = items[-200:]
        items.append({"content": content, "ts": int(time.time())})
        store[memory_type] = bucket
        self._db.set("auto_memory", store)

    def get_auto_memory(self, user_id: str, memory_type: str | None = None, limit: int = 5) -> list[str]:
        store = self._db.get("auto_memory", {})
        if memory_type:
            buckets = {memory_type: store.get(memory_type, {})}
        else:
            buckets = store
        results: list[tuple[int, str]] = []
        for bucket in buckets.values():
            for item in bucket.get(user_id, []):
                results.append((item.get("ts", 0), item.get("content", "")))
        results.sort(key=lambda x: x[0], reverse=True)
        return [c for _, c in results[:limit]]
