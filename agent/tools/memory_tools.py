"""记忆管理工具:跨会话全文搜索 + Markdown 文件记忆 + 用户概要。

数据源:
- sqlite_store:SQLite+FTS5 全文搜索的对话历史(经 ctx.extra)
- file_memory:data/memory/ 下的 Markdown 记忆文件(经 ctx.extra)
- memory(内置 MemoryStore):auto_memory 自动记忆与用户概要
"""
from __future__ import annotations

import json
from typing import Any

from .base import Tool, ToolContext


class MemorySearchTool(Tool):
    name = "memory_search"
    description = (
        "搜索所有记忆(对话历史全文 + Markdown 记忆文件 + 用户自动记忆),"
        "返回相关片段。用于跨会话回忆之前聊过的内容、查证用户偏好。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "搜索关键词,如 '项目名称' '用户偏好'"},
            "limit": {"type": "integer", "description": "返回条数,默认 10", "default": 10},
        },
        "required": ["query"],
    }

    async def execute(self, ctx: ToolContext, query: str, limit: int = 10) -> Any:
        limit = max(1, min(int(limit), 30))
        results: list[str] = []

        sqlite = ctx.extra.get("sqlite_store")
        if sqlite is not None:
            try:
                rows = await sqlite.search(query, limit=limit, user_id=ctx.event.user_id)
                for r in rows[:5]:
                    content = (r.get("content") or "")[:200]
                    results.append(f"[历史对话] {r.get('role')}: {content}")
            except Exception:  # noqa: BLE001
                pass

        fmem = ctx.extra.get("file_memory")
        if fmem is not None:
            try:
                hits = await fmem.search(query, limit=limit)
                for h in hits[:5]:
                    results.append(f"[记忆文件 {h.get('file')}] {h.get('line')[:200]}")
            except Exception:  # noqa: BLE001
                pass

        if ctx.memory is not None:
            for a in ctx.memory.get_auto_memory(ctx.event.user_id, limit=limit):
                results.append(f"[自动记忆] {a[:200]}")

        return results[:limit] if results else "未找到相关记忆"


class MemoryAddTool(Tool):
    name = "memory_add"
    description = (
        "向长期记忆添加一条笔记(按主题分类)。用户偏好、重要事实、项目信息等"
        "值得长期记住的内容可存入,后续可检索。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "topic": {
                "type": "string",
                "description": "主题分类,如 user_pref / project / fact / goal",
            },
            "content": {"type": "string", "description": "要记住的内容"},
        },
        "required": ["topic", "content"],
    }

    async def execute(self, ctx: ToolContext, topic: str, content: str) -> Any:
        fmem = ctx.extra.get("file_memory")
        if fmem is not None:
            res = await fmem.update(topic, content)
            if res.get("ok"):
                dup = " (内容已存在,未重复添加)" if res.get("duplicate") else ""
                return f"已保存到记忆文件 {res['file']}{dup}"
        if ctx.memory is not None:
            ctx.memory.save_auto_memory(ctx.event.user_id, topic, content)
            return "已保存到自动记忆"
        return "记忆功能不可用"


class MemoryListTool(Tool):
    name = "memory_list"
    description = "列出所有长期记忆文件、当前用户的记忆概要,方便查看已记住的内容。"
    parameters = {"type": "object", "properties": {}}

    async def execute(self, ctx: ToolContext) -> Any:
        out: list[str] = []

        fmem = ctx.extra.get("file_memory")
        if fmem is not None:
            try:
                files = await fmem.list_memories()
                out.append("记忆文件:\n" + "\n".join(f"- {f}" for f in files))
            except Exception:  # noqa: BLE001
                pass

        if ctx.memory is not None:
            summary = ctx.memory.get_user_summary(ctx.event.user_id)
            if summary:
                out.append("用户概要: " + json.dumps(summary, ensure_ascii=False))
            auto = ctx.memory.get_auto_memory(ctx.event.user_id, limit=5)
            if auto:
                out.append("自动记忆:\n" + "\n".join(f"- {a}" for a in auto))

        return "\n".join(out) if out else "暂无记忆,可通过 memory_add 添加"
