"""自动记忆 — 参考 Claude Code Auto Memory。

Agent 自动从对话中提取关键信息并保存为记忆文件。
分类: 用户偏好, 重要事实, 项目约定, 调试经验。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from loguru import logger

_MEMORY_CATEGORIES = ["user_preference", "important_fact", "project_convention", "debug_insight"]


class AutoMemory:
    """自动记忆管理器."""

    def __init__(self, base_dir: str = "data"):
        self._dir = Path(base_dir) / "auto_memory"
        self._dir.mkdir(parents=True, exist_ok=True)
        self._index: dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        idx = self._dir / "index.json"
        if idx.exists():
            try: self._index = json.loads(idx.read_text())
            except Exception: pass

    def _save(self) -> None:
        (self._dir / "index.json").write_text(json.dumps(self._index, ensure_ascii=False, indent=2))

    async def extract_and_save(self, user_message: str, assistant_reply: str, provider) -> str | None:
        """从对话中提取关键信息并保存.

        Returns:
            提取的记忆文本，或 None
        """
        if len(user_message) < 10 or len(assistant_reply) < 20:
            return None

        try:
            prompt = (
                "从以下对话中提取值得长期记忆的关键信息。只输出JSON，无其他文本。\n"
                "格式: {\"memories\": [{\"category\": \"user_preference|important_fact|project_convention|debug_insight\", "
                "\"content\": \"简短的一句话\", \"key\": \"唯一标识符\"}]}\n"
                "如果没有值得记忆的信息，输出 {\"memories\": []}\n\n"
                f"用户: {user_message[:500]}\n助手: {assistant_reply[:500]}"
            )
            resp = await provider.chat([{"role": "user", "content": prompt}], tools=None)
            content = resp.get("content", "{}")

            import re
            match = re.search(r'\{[\s\S]*\}', content)
            data = json.loads(match.group(0) if match else content)
            memories = data.get("memories", [])

            for mem in memories:
                key = mem["key"]
                category = mem.get("category", "important_fact")
                if category not in _MEMORY_CATEGORIES:
                    category = "important_fact"
                self._index[key] = {"category": category, "content": mem["content"],
                                      "timestamp": time.time(), "count": self._index.get(key, {}).get("count", 0) + 1}
                logger.debug(f"自动记忆: [{category}] {mem['content'][:80]}")

            if memories:
                self._save()
                return f"已记录 {len(memories)} 条记忆"
        except Exception:
            pass
        return None

    def get_relevant(self, query: str, limit: int = 5) -> list[dict]:
        """获取相关的记忆条目 (简单关键词匹配)."""
        results = []
        for key, entry in self._index.items():
            if query.lower() in entry["content"].lower() or query.lower() in key.lower():
                results.append(entry)
        results.sort(key=lambda x: x.get("count", 0), reverse=True)
        return results[:limit]

    def get_all(self) -> list[dict]:
        return [{"key": k, **v} for k, v in self._index.items()]

    def delete(self, key: str) -> bool:
        if key in self._index:
            del self._index[key]; self._save(); return True
        return False
