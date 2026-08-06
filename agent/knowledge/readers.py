"""多格式内容读取:文件(复用 DocumentPerceiver)+ URL(SSRF 防护 + HTML 清洗)。

供 knowledge_add 的 file/url 参数使用。
"""
from __future__ import annotations

import asyncio
from typing import Any

from loguru import logger


class ContentReader:
    """文件/URL → 纯文本。"""

    async def read_file(self, path: str, max_chars: int = 500_000) -> str:
        """复用感知层 DocumentPerceiver 解析 PDF/Word/PPT/Excel/文本 等。"""
        from ..perception import DocumentPerceiver

        doc = await DocumentPerceiver().parse(path)
        text = doc.get("content", "")
        if len(text) > max_chars:
            text = text[:max_chars]
        return text.strip()

    async def read_url(self, url: str, max_chars: int = 500_000) -> str:
        """SSRF 安全检查通过后抓取,提取正文(优先 trafilatura,回退 BeautifulSoup)。"""
        from ...security.auth import is_safe_url_async

        if not await is_safe_url_async(url):
            raise RuntimeError(f"URL 安全检查未通过(疑似内网地址): {url}")
        import httpx

        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            resp = await client.get(
                url, headers={"User-Agent": "QQBot-RAG/1.0"}
            )
            resp.raise_for_status()
        html = resp.text

        def _extract() -> str:
            try:
                import trafilatura

                text = trafilatura.extract(
                    html, include_comments=False, include_tables=True, include_links=False
                )
                if text:
                    return text.strip()
            except Exception as exc:  # noqa: BLE001
                logger.debug("trafilatura 提取失败,回退 BeautifulSoup: {}", exc)
            try:
                from bs4 import BeautifulSoup

                soup = BeautifulSoup(html, "html.parser")
                for tag in soup(["script", "style", "nav", "footer", "header"]):
                    tag.decompose()
                return "\n".join(s.strip() for s in soup.stripped_strings)[:max_chars]
            except ImportError:
                return html

        text = await asyncio.to_thread(_extract)
        if len(text) > max_chars:
            text = text[:max_chars]
        return text
