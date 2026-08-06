"""知识库工具:添加/检索知识条目(轻量 RAG,JSON 持久化 + 关键词评分)。"""
from __future__ import annotations

import re
import time
import uuid
from typing import Any

from .base import Tool, ToolContext


def _tokenize(text: str) -> set[str]:
    """中文按字拆分,英文按词拆分,用于检索打分。"""
    tokens = set(re.findall(r"[a-zA-Z0-9_]{2,}", text.lower()))
    tokens.update(c for c in text if "一" <= c <= "鿿")
    return tokens


class KnowledgeAddTool(Tool):
    name = "knowledge_add"
    description = "向知识库添加一条知识(标题+内容+分类),长期保存可复用"
    parameters = {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "知识标题"},
            "content": {"type": "string", "description": "知识内容"},
            "category": {"type": "string", "description": "分类标签,如'项目'/'通用'"},
        },
        "required": ["title", "content"],
    }

    async def execute(self, ctx: ToolContext, title: str, content: str, category: str = "通用") -> Any:
        entries = ctx.db.get("knowledge", [])
        entry = {
            "id": uuid.uuid4().hex[:12],
            "title": title,
            "content": content,
            "category": category,
            "created_at": int(time.time()),
            "session": ctx.event.session_id,
        }
        entries.append(entry)
        if len(entries) > 2000:
            entries = entries[-2000:]
        ctx.db.set("knowledge", entries)
        return {"ok": True, "id": entry["id"], "category": category}


class KnowledgeSearchTool(Tool):
    name = "knowledge_search"
    description = "在知识库中检索相关知识(按关键词相关度排序)"
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "检索关键词"},
            "category": {"type": "string", "description": "按分类过滤(可选)"},
            "limit": {"type": "integer", "description": "返回条数,默认3"},
        },
        "required": ["query"],
    }

    async def execute(self, ctx: ToolContext, query: str, category: str | None = None, limit: int = 3) -> Any:
        entries = ctx.db.get("knowledge", [])
        q_tokens = _tokenize(query)
        if not q_tokens:
            return {"count": 0, "results": []}

        scored = []
        for e in entries:
            if category and e.get("category") != category:
                continue
            text = e.get("title", "") + " " + e.get("content", "")
            e_tokens = _tokenize(text)
            score = len(q_tokens & e_tokens)
            if score > 0:
                scored.append((score, e))

        scored.sort(key=lambda x: x[0], reverse=True)
        results = [
            {
                "title": e["title"],
                "content": e["content"][:500],
                "category": e.get("category", ""),
                "score": s,
            }
            for s, e in scored[: int(limit or 3)]
        ]
        return {"count": len(results), "results": results}
