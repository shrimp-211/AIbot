"""知识库工具:添加/检索知识条目(向量混合检索 RAG,降级为旧关键词检索)。

优先使用 ToolContext.extra["knowledge"](KnowledgeManager,由 main.py 注入):
- knowledge_add 支持 content / url / file 三种来源,分块 + 嵌入入库
- knowledge_search 走 BM25 + FAISS 稠密 + RRF 融合 + Rerank
无管理实例时回退为 JSON 存储 + 关键词打分(旧行为,保持兼容)。
"""
from __future__ import annotations

import re
import time
import uuid
from typing import Any

from .base import Tool, ToolContext


def _tokenize(text: str) -> set[str]:
    """中文按字拆分,英文按词拆分,用于旧检索打分。"""
    tokens = set(re.findall(r"[a-zA-Z0-9_]{2,}", text.lower()))
    tokens.update(c for c in text if "一" <= c <= "鿿")
    return tokens


def _manager(ctx: ToolContext) -> Any:
    return ctx.extra.get("knowledge")


class KnowledgeAddTool(Tool):
    name = "knowledge_add"
    description = "向知识库添加一条知识(标题+内容,或 url/file 导入),支持向量检索复用"
    parameters = {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "知识标题"},
            "content": {"type": "string", "description": "知识内容(与 url/file 三选一)"},
            "category": {"type": "string", "description": "分类标签,如'项目'/'通用'"},
            "url": {"type": "string", "description": "网页地址(自动抓取正文,需安全校验)"},
            "file": {"type": "string", "description": "本地文件路径(PDF/Word/PPT/文本 等)"},
        },
        "required": ["title"],
    }

    async def execute(
        self,
        ctx: ToolContext,
        title: str,
        content: str = "",
        category: str = "通用",
        url: str = "",
        file: str = "",
    ) -> Any:
        mgr = _manager(ctx)
        source = ""
        if url:
            try:
                from ..knowledge.readers import ContentReader

                content = await ContentReader().read_url(url)
                source = f"url:{url}"
            except RuntimeError as exc:
                return {"error": f"URL 导入失败: {exc}"}
            except Exception as exc:  # noqa: BLE001
                return {"error": f"URL 抓取失败: {exc}"}
        elif file:
            try:
                from ..knowledge.readers import ContentReader

                content = await ContentReader().read_file(file)
                source = f"file:{file}"
            except RuntimeError as exc:
                return {"error": f"文件导入失败: {exc}"}

        content = (content or "").strip()
        if not content:
            return {"error": "请提供 content / url / file 其中一项"}

        if mgr is not None:
            try:
                return await mgr.add_document(
                    title=title, content=content, category=category, source=source
                )
            except Exception as exc:  # noqa: BLE001
                return {"error": f"知识入库失败: {exc}"}

        # 旧行为兜底:JSON 存储
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
        return {"ok": True, "id": entry["id"], "category": category, "mode": "legacy"}


class KnowledgeSearchTool(Tool):
    name = "knowledge_search"
    description = "在知识库中检索相关知识(向量+关键词混合检索,按相关度排序)"
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "检索词(语义检索)"},
            "category": {"type": "string", "description": "按分类过滤(可选)"},
            "limit": {"type": "integer", "description": "返回条数,默认3"},
        },
        "required": ["query"],
    }

    async def execute(self, ctx: ToolContext, query: str, category: str | None = None, limit: int = 3) -> Any:
        mgr = _manager(ctx)
        if mgr is not None:
            try:
                return await mgr.search(query, category, limit)
            except Exception as exc:  # noqa: BLE001
                return {"error": f"向量检索失败: {exc}"}

        # 旧行为兜底:JSON 关键词检索
        entries = ctx.db.get("knowledge", [])
        q_tokens = _tokenize(query)
        if not q_tokens:
            return {"count": 0, "results": []}
        scored = []
        for e in entries:
            if category and e.get("category") != category:
                continue
            text = e.get("title", "") + " " + e.get("content", "")
            score = len(q_tokens & _tokenize(text))
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
        return {"count": len(results), "results": results, "mode": "keyword"}


class KnowledgeListTool(Tool):
    name = "knowledge_list"
    description = "列出知识库统计与文档清单(文档数/分块数/向量索引状态)"
    parameters = {
        "type": "object",
        "properties": {
            "category": {"type": "string", "description": "按分类过滤(可选)"},
            "limit": {"type": "integer", "description": "最多列出条数,默认10"},
        },
    }

    async def execute(self, ctx: ToolContext, category: str | None = None, limit: int = 10) -> Any:
        mgr = _manager(ctx)
        if mgr is None:
            entries = ctx.db.get("knowledge", [])
            if category:
                entries = [e for e in entries if e.get("category") == category]
            return {"count": len(entries), "docs": entries[: int(limit or 10)], "mode": "legacy"}
        stats = mgr.stats()
        docs = [d for d in mgr._docs if not category or d.get("category") == category]
        docs.sort(key=lambda d: d.get("created_at", 0), reverse=True)
        return {
            "stats": stats,
            "docs": [
                {
                    "doc_id": d["id"],
                    "title": d.get("title", ""),
                    "category": d.get("category", ""),
                    "chunks": len(d.get("chunk_ids", [])),
                    "created_at": d.get("created_at", 0),
                }
                for d in docs[: int(limit or 10)]
            ],
        }
