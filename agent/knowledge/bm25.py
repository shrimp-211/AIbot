"""轻量 BM25 稀疏检索(纯 Python,无第三方依赖)。

用于混合检索的"稀疏路",与 FAISS 稠密路互补。
"""
from __future__ import annotations

import math
import re

_TOKEN_RE = re.compile(r"[a-zA-Z0-9_一-鿿]+")


def tokenize(text: str) -> list[str]:
    """分词:英文按词、中文按连续中文字符串(后续在 idf 计算中已按字符粒度聚类)。"""
    return _TOKEN_RE.findall((text or "").lower())


def char_tokens(text: str) -> list[str]:
    """中文按单字 + 英文按词,适配中文检索粒度。"""
    out: list[str] = []
    for tok in tokenize(text):
        if tok.isascii():
            out.append(tok)
        else:
            out.extend(tok)  # 中文逐字
    return out


def bm25_scores(
    query: str,
    texts: list[str],
    k1: float = 1.5,
    b: float = 0.75,
) -> list[float]:
    """对 texts 中的每个文档按 BM25 打分(与 query 对齐,0 表示无命中)。

    Args:
        query: 检索词
        texts: 文档文本列表(顺序与返回值对齐)
    Returns:
        长度与 texts 相同的分数列表
    """
    q_toks = set(char_tokens(query))
    if not q_toks or not texts:
        return [0.0] * len(texts)

    corpus = [char_tokens(t) for t in texts]
    n = len(corpus)
    # 文档频率 df
    df: dict[str, int] = {}
    for toks in corpus:
        for t in set(toks):
            df[t] = df.get(t, 0) + 1
    # idf(平滑)
    idf: dict[str, float] = {}
    for t, d in df.items():
        idf[t] = math.log((n - d + 0.5) / (d + 0.5) + 1.0)
    avg_len = sum(len(c) for c in corpus) / n if n else 1.0

    scores: list[float] = []
    for toks in corpus:
        tf: dict[str, int] = {}
        for t in toks:
            tf[t] = tf.get(t, 0) + 1
        dl = len(toks)
        s = 0.0
        for t in q_toks:
            if t not in idf:
                continue
            f = tf.get(t, 0)
            if not f:
                continue
            denom = f + k1 * (1 - b + b * dl / avg_len)
            s += idf[t] * (f * (k1 + 1)) / denom
        scores.append(s)
    return scores
