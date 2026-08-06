"""SQLite + FTS5 全文搜索的记忆存储(参考 Hermes SQLite+FTS5 设计)。

所有会话消息持久化到 SQLite,messages 表 + FTS5 trigram 虚拟表(触发器
自动同步),支持跨会话全文搜索。

异步策略:同步 sqlite3 调用包在 `asyncio.to_thread()`,配合线程锁保证
连接安全(CLAUDE.md 异步规范)。

FTS 注意:trigram tokenizer 要求查询串 >=3 字符;2 字符中文查询用
`LIKE '%词%'` 兜底。
"""
from __future__ import annotations

import asyncio
import re
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL,
  user_id TEXT NOT NULL DEFAULT '',
  group_id TEXT,
  role TEXT NOT NULL DEFAULT 'user',
  content TEXT NOT NULL,
  platform TEXT NOT NULL DEFAULT 'qq',
  ts REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, ts);
CREATE INDEX IF NOT EXISTS idx_messages_user ON messages(user_id, ts);
CREATE INDEX IF NOT EXISTS idx_messages_ts ON messages(ts);
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(content, tokenize='trigram');
CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
  INSERT INTO messages_fts(rowid, content) VALUES (new.id, new.content);
END;
CREATE TRIGGER IF NOT EXISTS messages_ad AFTER DELETE ON messages BEGIN
  INSERT INTO messages_fts(messages_fts, rowid, content) VALUES('delete', old.id, old.content);
END;
CREATE TRIGGER IF NOT EXISTS messages_au AFTER UPDATE ON messages BEGIN
  INSERT INTO messages_fts(messages_fts, rowid, content) VALUES('delete', old.id, old.content);
  INSERT INTO messages_fts(rowid, content) VALUES (new.id, new.content);
END;
"""

_CJK_SEG = re.compile(r"[一-鿿]{3,}")
_ENG_WORD = re.compile(r"[A-Za-z0-9_]{3,}")


def _extract_fts_terms(text: str) -> list[str]:
    """提取可用于 trigram MATCH 的片段:>=3 字符的中文串与英文词。"""
    terms: list[str] = []
    for seg in _CJK_SEG.findall(text):
        terms.append(seg)
    for w in _ENG_WORD.findall(text):
        terms.append(w.lower())
    return terms


class SQLiteStore:
    def __init__(self, path: str | Path):
        self._path = Path(path)
        self._lock = threading.Lock()
        # 单连接跨线程使用,靠锁保证串行访问
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    async def start(self) -> None:
        pass  # schema 已在构造时初始化

    async def close(self) -> None:
        await asyncio.to_thread(self._close_sync)

    def _close_sync(self) -> None:
        with self._lock:
            self._conn.close()

    # ---------- 写入 ----------

    async def add_message(
        self,
        *,
        session_id: str,
        role: str,
        content: str,
        user_id: str = "",
        group_id: str | None = None,
        platform: str = "qq",
        ts: float | None = None,
    ) -> int:
        if not content:
            return 0
        return await asyncio.to_thread(
            self._add_sync,
            session_id,
            role,
            content,
            user_id,
            group_id,
            platform,
            ts if ts is not None else time.time(),
        )

    def _add_sync(
        self,
        session_id: str,
        role: str,
        content: str,
        user_id: str,
        group_id: str | None,
        platform: str,
        ts: float,
    ) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO messages (session_id, user_id, group_id, role, content, platform, ts) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (session_id, user_id, group_id, role, content, platform, ts),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    # ---------- 查询 ----------

    async def search(
        self,
        query: str,
        limit: int = 10,
        user_id: str | None = None,
        group_id: str | None = None,
        since: float | None = None,
        role: str | None = None,
    ) -> list[dict[str, Any]]:
        return await asyncio.to_thread(
            self._search_sync, query, limit, user_id, group_id, since, role
        )

    def _search_sync(
        self,
        query: str,
        limit: int,
        user_id: str | None,
        group_id: str | None,
        since: float | None,
        role: str | None,
    ) -> list[dict[str, Any]]:
        query = (query or "").strip()
        if not query:
            return []
        conds: list[str] = []
        params: list[Any] = []

        if user_id:
            conds.append("m.user_id = ?")
            params.append(user_id)
        if group_id:
            conds.append("m.group_id = ?")
            params.append(group_id)
        if since:
            conds.append("m.ts >= ?")
            params.append(since)
        if role:
            conds.append("m.role = ?")
            params.append(role)

        # FTS 候选(>=3 字符片段),trigram 子串匹配
        terms = _extract_fts_terms(query)
        if terms:
            match_expr = " OR ".join(f'"{t.replace(chr(34), chr(34) + chr(34))}"' for t in terms)
            with self._lock:
                cand = self._conn.execute(
                    "SELECT rowid FROM messages_fts WHERE messages_fts MATCH ?",
                    (match_expr,),
                ).fetchall()
            cand_ids = [r[0] for r in cand]
            if cand_ids:
                conds.append(f"m.id IN ({','.join('?' * len(cand_ids))})")
                params.extend(cand_ids)
            else:
                # FTS 无候选:LIKE 兜底(覆盖 2 字符中文等 FTS 盲区)
                conds.append("m.content LIKE ?")
                params.append(f"%{query}%")
        else:
            # 无有效 FTS 词:直接 LIKE 精确子串
            conds.append("m.content LIKE ?")
            params.append(f"%{query}%")

        params.append(limit)
        sql = (
            "SELECT m.id, m.session_id, m.user_id, m.group_id, m.role, "
            "m.content, m.platform, m.ts FROM messages m "
            f"WHERE {' AND '.join(conds)} ORDER BY m.ts DESC LIMIT ?"
        )
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [
            {
                "id": r[0],
                "session_id": r[1],
                "user_id": r[2],
                "group_id": r[3],
                "role": r[4],
                "content": r[5],
                "platform": r[6],
                "ts": r[7],
            }
            for r in rows
        ]

    async def recent(self, session_id: str, limit: int = 20) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._recent_sync, session_id, limit)

    def _recent_sync(self, session_id: str, limit: int) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, session_id, user_id, group_id, role, content, platform, ts "
                "FROM messages WHERE session_id = ? ORDER BY id DESC LIMIT ?",
                (session_id, limit),
            ).fetchall()
        return [
            {
                "id": r[0],
                "session_id": r[1],
                "user_id": r[2],
                "group_id": r[3],
                "role": r[4],
                "content": r[5],
                "platform": r[6],
                "ts": r[7],
            }
            for r in reversed(rows)
        ]

    async def stats(self) -> dict[str, Any]:
        return await asyncio.to_thread(self._stats_sync)

    def _stats_sync(self) -> dict[str, Any]:
        with self._lock:
            total = self._conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
            sessions = self._conn.execute(
                "SELECT COUNT(DISTINCT session_id) FROM messages"
            ).fetchone()[0]
        return {"total": total, "sessions": sessions}
