"""知识分块策略:fixed-size / markdown / semantic。

- fixed:  按字符数切分 + 重叠窗口
- markdown: 按标题层级(#/##)切分,小节合并到大块
- semantic: 按句/段边界切分(中文。！？；换行),聚合成约 chunk_size
"""
from __future__ import annotations

import re

_SENT_SPLIT = re.compile(r"(?<=[。！？；\n])")

_HEADING_RE = re.compile(r"^(#{1,4})\s+(.+)$", re.MULTILINE)


def chunk_text(
    text: str,
    strategy: str = "semantic",
    chunk_size: int = 800,
    overlap: int = 100,
) -> list[str]:
    """按策略分块,返回非空块列表(去空白)。"""
    text = (text or "").strip()
    if not text:
        return []
    chunk_size = max(64, int(chunk_size))
    overlap = max(0, min(int(overlap), chunk_size // 2))
    if strategy == "fixed":
        return _fixed_chunks(text, chunk_size, overlap)
    if strategy == "markdown":
        chunks = _markdown_chunks(text, chunk_size)
        if chunks:
            return chunks
    return _semantic_chunks(text, chunk_size, overlap)


def _fixed_chunks(text: str, chunk_size: int, overlap: int) -> list[str]:
    if len(text) <= chunk_size:
        return [text]
    step = chunk_size - overlap
    chunks = [text[i : i + chunk_size].strip() for i in range(0, len(text), step)]
    return [c for c in chunks if c]


def _markdown_chunks(text: str, chunk_size: int) -> list[str]:
    """按标题切分;无标题结构时返回空列表(调用方回退 semantic)。"""
    matches = list(_HEADING_RE.finditer(text))
    if not matches:
        return []
    sections: list[str] = []
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        sections.append(text[m.start() : end].strip())
    # 合并过小的小节,切分过大的小节
    merged: list[str] = []
    buf = ""
    for sec in sections:
        if not sec:
            continue
        if len(sec) > chunk_size * 2:
            if buf:
                merged.append(buf)
                buf = ""
            merged.extend(_fixed_chunks(sec, chunk_size, chunk_size // 4))
        elif len(buf) + len(sec) <= chunk_size:
            buf = (buf + "\n" + sec).strip()
        else:
            merged.append(buf)
            buf = sec
    if buf:
        merged.append(buf)
    return [c for c in merged if c]


def _semantic_chunks(text: str, chunk_size: int, overlap: int) -> list[str]:
    """按句边界切句 → 贪心拼接成 ~chunk_size 的块(尽量在段落边界断开)。"""
    sentences = [s.strip() for s in _SENT_SPLIT.split(text) if s.strip()]
    if not sentences:
        return _fixed_chunks(text, chunk_size, overlap)
    chunks: list[str] = []
    cur = ""
    for sent in sentences:
        # 单个句子过长时独立成块(再内部按字符切)
        if len(sent) > chunk_size:
            if cur:
                chunks.append(cur)
                cur = ""
            chunks.extend(_fixed_chunks(sent, chunk_size, overlap))
            continue
        if len(cur) + len(sent) <= chunk_size:
            cur = (cur + sent).strip()
        else:
            chunks.append(cur)
            # 用上一块尾部做重叠,保留上下文衔接
            tail = cur[-overlap:] if overlap else ""
            cur = (tail + sent).strip()
    if cur:
        chunks.append(cur)
    return [c for c in chunks if c]
