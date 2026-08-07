"""Markdown 文件记忆(参考 Claude Code MEMORY.md 机制)。

在 `data/memory/` 下维护可读、可编辑的 Markdown 记忆:
- MEMORY.md —— 长期记忆索引(Claude Code 格式)
- USER.md   —— 用户画像与偏好
- SOUL.md   —— Agent 人格定义
- memory/*.md —— 分类记忆文件(按 topic 组织,条目带日期)

所有文件在启动时汇总为上下文注入;支持全文搜索与自动追加。
"""
from __future__ import annotations

import asyncio
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any

_STANDARD_FILES = {
    "MEMORY.md": """# 长期记忆

本文件维护 Agent 的长期记忆索引。每条记忆指向 memory/ 下的分类文件。

- [用户偏好](memory/user_pref.md)
- [项目事实](memory/project.md)
- [用户画像](USER.md)
""",
    "USER.md": """# 用户画像

- 名字: (待补充)
- 偏好: (待补充)
- 知识水平: (待补充)
""",
    "SOUL.md": """# Agent 人格

QQ AI Agent 的内置助手,乐于帮助群成员解决编程、查询与日程安排等问题。
""",
}


class FileMemoryStore:
    def __init__(self, base_dir: str | Path):
        self._base = Path(base_dir)
        self._base.mkdir(parents=True, exist_ok=True)
        self._mem_dir = self._base / "memory"
        self._mem_dir.mkdir(parents=True, exist_ok=True)
        self._ensure_defaults()

    def _ensure_defaults(self) -> None:
        for name, content in _STANDARD_FILES.items():
            path = self._base / name
            if not path.exists():
                path.write_text(content, encoding="utf-8")

    # ---------- 读写 ----------

    def _read_file(self, path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            return ""

    def _write_file(self, path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(content, encoding="utf-8")
        tmp.replace(path)


def _within_base(base: Path, path: Path) -> bool:
    """路径是否严格在 base 内(用相对路径判断,防兄弟目录前缀绕过)。"""
    try:
        path.relative_to(base)
        return True
    except ValueError:
        return False

    async def read(self, name: str) -> str:
        """读取指定记忆文件(名称可含子目录,如 memory/user_pref.md)。"""
        path = (self._base / name).resolve()
        if not _within_base(self._base.resolve(), path):
            return ""
        return await asyncio.to_thread(self._read_file, path)

    async def write(self, name: str, content: str) -> None:
        path = (self._base / name).resolve()
        if not _within_base(self._base.resolve(), path):
            raise ValueError(f"非法记忆文件路径: {name}")
        await asyncio.to_thread(self._write_file, path, content)

    # ---------- 记忆管理 ----------

    def _all_md_files(self) -> list[Path]:
        return sorted(self._base.glob("*.md")) + sorted(self._mem_dir.glob("*.md"))

    async def list_memories(self) -> list[str]:
        def _list() -> list[str]:
            return [str(p.relative_to(self._base)).replace("\\", "/") for p in self._all_md_files()]

        return await asyncio.to_thread(_list)

    async def search(self, keyword: str, limit: int = 20) -> list[dict[str, Any]]:
        """全文搜索记忆文件,返回 文件名 + 命中行。"""

        def _search() -> list[dict[str, Any]]:
            kw = keyword.strip().lower()
            hits: list[dict[str, Any]] = []
            for path in self._all_md_files():
                for line in self._read_file(path).splitlines():
                    if kw and kw in line.lower():
                        hits.append(
                            {
                                "file": str(path.relative_to(self._base)).replace("\\", "/"),
                                "line": line.strip(),
                            }
                        )
                        if len(hits) >= limit:
                            return hits
            return hits

        return await asyncio.to_thread(_search)

    async def update(self, topic: str, entry: str) -> dict[str, Any]:
        """自动记忆:向 memory/<topic>.md 追加带日期条目,并维护 MEMORY.md 索引。

        相同条目不重复追加。
        """
        topic = re.sub(r"[^\w一-鿿-]", "_", (topic or "notes").strip()) or "notes"
        entry = (entry or "").strip()
        if not entry:
            return {"error": "记忆内容为空"}
        name = f"memory/{topic}.md"
        path = self._mem_dir / f"{topic}.md"

        def _update() -> dict[str, Any]:
            existing = self._read_file(path)
            date = datetime.now().strftime("%Y-%m-%d")
            line = f"- {date}: {entry}"
            if line in existing:
                return {"ok": True, "file": name, "duplicate": True}
            content = existing.rstrip()
            new_content = f"{content}\n{line}\n" if content else f"# {topic}\n\n{line}\n"
            self._write_file(path, new_content)
            self._ensure_index(name, topic)
            return {"ok": True, "file": name}

        return await asyncio.to_thread(_update)

    def _ensure_index(self, name: str, topic: str) -> None:
        """在 MEMORY.md 中登记该记忆文件(参考 Claude Code MEMORY.md 索引)。"""
        index_path = self._base / "MEMORY.md"
        content = self._read_file(index_path)
        link = f"[{topic}]({name})"
        if link in content:
            return
        lines = content.rstrip().splitlines()
        lines.append(f"- {link}")
        self._write_file(index_path, "\n".join(lines) + "\n")

    async def build_context(self, max_chars: int = 12000) -> str:
        """汇总所有记忆文件内容,供 system prompt 注入。"""

        def _build() -> str:
            parts: list[str] = []
            total = 0
            for path in self._all_md_files():
                content = self._read_file(path).strip()
                if not content:
                    continue
                rel = str(path.relative_to(self._base)).replace("\\", "/")
                block = f"### {rel}\n{content}"
                total += len(block)
                if total > max_chars:
                    parts.append(block[: max_chars - (total - len(block))])
                    break
                parts.append(block)
            return "\n\n".join(parts)

        return await asyncio.to_thread(_build)
