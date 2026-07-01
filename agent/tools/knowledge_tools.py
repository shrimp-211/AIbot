"""知识库工具 — knowledge_search + knowledge_add.

参考 AstrBot KnowledgeBaseManager 的混合检索架构:
- 文本相似度搜索 (关键字 + 向量可选)
- 文档导入 (TXT/MD/JSON)
- 元数据过滤
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from .base import BaseTool


class KnowledgeSearchTool(BaseTool):
    name = "knowledge_search"
    description = (
        "搜索知识库中的文档和内容。支持文本相似度搜索和关键字搜索。"
        "当需要查找项目文档、学习资料或过去保存的知识时使用。"
    )
    permission_level = 0

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "搜索查询文本",
                },
                "collection": {
                    "type": "string",
                    "description": "知识库名称 (默认 'default')",
                    "default": "default",
                },
                "top_k": {
                    "type": "integer",
                    "description": "返回结果数 (默认 5, 最大 10)",
                    "default": 5,
                },
            },
            "required": ["query"],
        }

    async def execute(self, query: str, collection: str = "default", top_k: int = 5, **kwargs) -> str:
        """搜索知识库."""
        kb_dir = Path("data/knowledge_base") / collection
        if not kb_dir.exists():
            return f"知识库 '{collection}' 不存在。使用 knowledge_add 来添加文档。"

        top_k = min(top_k, 10)

        # 混合检索: 关键字 + 简单语义匹配
        results = []
        query_tokens = set(_tokenize(query))

        for doc_file in kb_dir.glob("*.json"):
            try:
                doc = json.loads(doc_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue

            content = doc.get("content", "")
            title = doc.get("title", doc_file.stem)
            content_tokens = set(_tokenize(content))
            title_tokens = set(_tokenize(title))

            # 关键词匹配得分
            title_match = query_tokens & title_tokens
            content_match = query_tokens & content_tokens
            score = len(title_match) * 3 + len(content_match)

            if score > 0:
                # 提取相关片段
                snippet = _extract_snippet(content, query, max_len=200)
                results.append({
                    "title": title,
                    "score": score,
                    "snippet": snippet,
                    "source": doc.get("source", doc_file.stem),
                    "updated": doc.get("updated", ""),
                })

        # 按得分降序
        results.sort(key=lambda r: r["score"], reverse=True)
        if not results:
            # 回退: 纯子串匹配
            for doc_file in kb_dir.glob("*.json"):
                try:
                    doc = json.loads(doc_file.read_text(encoding="utf-8"))
                except Exception:
                    continue
                content = doc.get("content", "")
                if query.lower() in content.lower():
                    snippet = _extract_snippet(content, query, max_len=200)
                    results.append({
                        "title": doc.get("title", doc_file.stem),
                        "score": 1,
                        "snippet": snippet,
                        "source": doc.get("source", doc_file.stem),
                        "updated": doc.get("updated", ""),
                    })

        if not results:
            return f"在知识库 '{collection}' 中未找到与 '{query}' 相关的内容。"

        lines = [f"知识库搜索 '{query}' 的结果 (共 {len(results)} 条):"]
        for i, r in enumerate(results[:top_k], 1):
            lines.append(f"\n{i}. **{r['title']}** (得分: {r['score']})")
            lines.append(f"   {r['snippet']}")
            if r["source"]:
                lines.append(f"   来源: {r['source']}")

        return "\n".join(lines)


class KnowledgeAddTool(BaseTool):
    name = "knowledge_add"
    description = "向知识库添加文档。支持文本内容，自动分块存储用于后续检索。"
    permission_level = 1

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "文档标题",
                },
                "content": {
                    "type": "string",
                    "description": "文档内容",
                },
                "collection": {
                    "type": "string",
                    "description": "知识库名称 (默认 'default')",
                    "default": "default",
                },
                "source": {
                    "type": "string",
                    "description": "来源 URL 或文件名",
                },
            },
            "required": ["title", "content"],
        }

    async def execute(self, title: str, content: str, collection: str = "default", source: str = "", **kwargs) -> str:
        """添加文档到知识库."""
        kb_dir = Path("data/knowledge_base") / collection
        kb_dir.mkdir(parents=True, exist_ok=True)

        # 使用标题的哈希作为文件名
        import hashlib
        file_hash = hashlib.md5(title.encode()).hexdigest()[:10]
        doc_path = kb_dir / f"{file_hash}.json"

        doc = {
            "title": title,
            "content": content,
            "source": source,
            "chunks": _chunk_text(content, chunk_size=500),
            "updated": time.strftime("%Y-%m-%d %H:%M"),
        }

        is_update = doc_path.exists()
        doc_path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")

        chunk_count = len(doc["chunks"])
        action = "已更新" if is_update else "已添加"
        return f"文档 '{title}' {action}到知识库 '{collection}' ({chunk_count} 个分块, {len(content)} 字符)"


def _tokenize(text: str) -> list[str]:
    """简单中文/英文分词."""
    tokens = []
    # 英文词
    tokens.extend(re.findall(r"[a-zA-Z]{2,}", text.lower()))
    # 中文词: 用 一-鿿 范围 + 2-gram
    cjk_pattern = re.compile(r"[一-鿿㐀-䶿豈-﫿]+")
    for segment in cjk_pattern.findall(text):
        for i in range(len(segment) - 1):
            tokens.append(segment[i:i+2])
        tokens.append(segment)
    return tokens


def _extract_snippet(text: str, query: str, max_len: int = 200) -> str:
    """提取包含查询词的相关片段."""
    if not query:
        return text[:max_len]

    idx = text.lower().find(query.lower())
    if idx >= 0:
        start = max(0, idx - 40)
        end = min(len(text), idx + len(query) + max_len)
        snippet = text[start:end]
        if start > 0:
            snippet = "..." + snippet
        if end < len(text):
            snippet = snippet + "..."
        return snippet
    return text[:max_len]


def _chunk_text(text: str, chunk_size: int = 500) -> list[str]:
    """将长文本分块，尽量在段落/句子边界切分."""
    if len(text) <= chunk_size:
        return [text]

    chunks = []
    paragraphs = text.split("\n\n")
    current = ""

    for para in paragraphs:
        if len(current) + len(para) + 2 <= chunk_size:
            current = (current + "\n\n" + para) if current else para
        else:
            if current:
                chunks.append(current)
            # 如果单段超限，按句子切
            if len(para) > chunk_size:
                sentences = re.split(r"(?<=[。！？.!?])", para)
                buf = ""
                for sent in sentences:
                    if len(buf) + len(sent) <= chunk_size:
                        buf += sent
                    else:
                        if buf:
                            chunks.append(buf)
                        buf = sent
                if buf:
                    current = buf
                else:
                    current = ""
            else:
                current = para

    if current:
        chunks.append(current)

    return chunks
