"""网络工具:多引擎联网搜索 + 网页抓取(SSRF 防护)。"""
from __future__ import annotations

import json
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

from ...security.auth import is_safe_url
from .base import Tool, ToolContext

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"


class WebSearchTool(Tool):
    name = "web_search"
    description = "联网搜索获取最新信息,支持多引擎自动回退(Tavily→Brave→DuckDuckGo)"
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "搜索关键词"},
            "max_results": {"type": "integer", "description": "返回结果条数,默认5,最大10"},
        },
        "required": ["query"],
    }

    async def execute(self, ctx: ToolContext, query: str, max_results: int = 5) -> Any:
        n = max(1, min(int(max_results or 5), 10))
        engines = ["tavily", "brave", "duckduckgo"]
        errors: list[str] = []
        for engine in engines:
            try:
                result = await self._search(engine, ctx, query, n)
                if result:
                    return result
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{engine}: {exc}")
        return {"error": "所有搜索引擎均失败", "details": errors[:2]}

    async def _search(self, engine: str, ctx: ToolContext, query: str, n: int) -> list[dict] | None:
        async with httpx.AsyncClient(timeout=15, headers={"User-Agent": UA}) as client:
            if engine == "tavily":
                api_key = ctx.config.get("search.tavily_key", "")
                if not api_key:
                    return None
                resp = await client.post(
                    "https://api.tavily.com/search",
                    json={"api_key": api_key, "query": query, "max_results": n},
                )
                resp.raise_for_status()
                data = resp.json()
                return [
                    {
                        "title": r.get("title", ""),
                        "url": r.get("url", ""),
                        "content": r.get("content", "")[:500],
                    }
                    for r in data.get("results", [])[:n]
                ]

            if engine == "brave":
                api_key = ctx.config.get("search.brave_key", "")
                if not api_key:
                    return None
                resp = await client.get(
                    "https://api.search.brave.com/res/v1/web/search",
                    params={"q": query, "count": n},
                    headers={"X-Subscription-Token": api_key},
                )
                resp.raise_for_status()
                data = resp.json()
                return [
                    {"title": r.get("title", ""), "url": r.get("url", ""), "content": (r.get("description") or "")[:500]}
                    for r in data.get("web", {}).get("results", [])[:n]
                ]

            if engine == "duckduckgo":
                resp = await client.get(
                    "https://html.duckduckgo.com/html/",
                    params={"q": query},
                )
                resp.raise_for_status()
                return self._parse_ddg(resp.text, n)

    @staticmethod
    def _parse_ddg(html: str, n: int) -> list[dict]:
        """从 DuckDuckGo HTML 页面提取结果。"""
        import re

        results: list[dict] = []
        for block in re.findall(r'<div class="result results_links.*?</div>', html, re.S)[:n]:
            title_m = re.search(r'<a[^>]*class="result__a"[^>]*>(.*?)</a>', block, re.S)
            url_m = re.search(r'<a[^>]*class="result__a"[^>]*href="([^"]+)"', block, re.S)
            snip_m = re.search(r'<a[^>]*class="result__snippet"[^>]*>(.*?)</a>', block, re.S)
            clean = lambda s: re.sub(r"<[^>]+>", "", s or "").strip()
            if url_m:
                results.append(
                    {
                        "title": clean(title_m.group(1)) if title_m else "",
                        "url": url_m.group(1),
                        "content": clean(snip_m.group(1)) if snip_m else "",
                    }
                )
        return results


class WebFetchTool(Tool):
    name = "web_fetch"
    description = "抓取网页内容为文本(SSRF 防护,阻止访问内网地址)"
    parameters = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "要抓取的网页 URL"},
            "max_length": {"type": "integer", "description": "返回文本最大长度,默认8000"},
        },
        "required": ["url"],
    }

    async def execute(self, ctx: ToolContext, url: str, max_length: int = 8000) -> Any:
        if not is_safe_url(url):
            return {"error": "URL 不合法或指向内网地址,已被拦截"}
        try:
            async with httpx.AsyncClient(
                timeout=20, follow_redirects=True, headers={"User-Agent": UA}
            ) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                text = self._html_to_text(resp.text)
                return text[: int(max_length or 8000)]
        except Exception as exc:  # noqa: BLE001
            return {"error": f"抓取失败: {exc}"}

    @staticmethod
    def _html_to_text(html: str) -> str:
        import re
        from html import unescape

        text = re.sub(r"<script.*?</script>|<style.*?</style>", " ", html, flags=re.S)
        text = re.sub(r"<br\s*/?>", "\n", text)
        text = re.sub(r"</(p|div|h[1-6]|li|tr)>", "\n", text)
        text = re.sub(r"<[^>]+>", " ", text)
        text = unescape(text)
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n\s*\n+", "\n\n", text).strip()
        return text
