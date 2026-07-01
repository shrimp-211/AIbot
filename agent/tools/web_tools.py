"""网络工具 — WebSearch(Tavily/Brave/DuckDuckGo) + WebFetch."""

import asyncio
import ipaddress
from typing import Any
from urllib.parse import urlparse

import httpx

from .base import BaseTool

_http_client: httpx.AsyncClient | None = None

def _get_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None:
        _http_client = httpx.AsyncClient(timeout=15, follow_redirects=True,
                                          limits=httpx.Limits(max_connections=10))
    return _http_client


_BLOCKED_NETWORKS = [
    ipaddress.ip_network("10.0.0.0/8"), ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"), ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"), ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("224.0.0.0/4"), ipaddress.ip_network("240.0.0.0/4"),
    ipaddress.ip_network("::1/128"), ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
]


async def _is_safe_url(url: str) -> tuple[bool, str]:
    try: parsed = urlparse(url)
    except Exception: return False, "URL 格式无效"
    if parsed.scheme not in ("http", "https"): return False, f"不支持的协议: {parsed.scheme}"
    hostname = parsed.hostname
    if not hostname: return False, "URL 缺少主机名"
    try: addr = ipaddress.ip_address(hostname)
    except ValueError:
        try:
            loop = asyncio.get_event_loop()
            info = await loop.getaddrinfo(hostname, None)
            addr = ipaddress.ip_address(info[0][4][0])
        except Exception: return False, f"无法解析主机名: {hostname}"
    for net in _BLOCKED_NETWORKS:
        if addr in net: return False, f"禁止访问内网地址: {hostname}"
    return True, ""


class WebSearchTool(BaseTool):
    name = "web_search"
    description = "搜索互联网获取实时信息。支持 Tavily, Brave, DuckDuckGo。"
    permission_level = 0

    def __init__(self, config=None):
        super().__init__()
        self._cfg = config or {}

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {
            "query": {"type": "string", "description": "搜索关键词"},
            "num": {"type": "integer", "description": "返回结果数", "default": 5},
            "engine": {"type": "string", "enum": ["auto", "tavily", "brave", "duckduckgo"], "description": "搜索引擎"},
        }, "required": ["query"]}

    async def execute(self, query: str, num: int = 5, engine: str = "auto", **kwargs) -> str:
        c = self._cfg
        if engine in ("auto", "tavily") and c.get("tavily_api_key"):
            return await self._tavily(query, num, c["tavily_api_key"])
        if engine in ("auto", "brave") and c.get("brave_api_key"):
            return await self._brave(query, num, c["brave_api_key"])
        return await self._ddg(query, num)

    async def _tavily(self, q, n, k):
        c = _get_client(); c.timeout = 10
        r = await c.post("https://api.tavily.com/search", json={"api_key": k, "query": q, "search_depth": "basic", "max_results": n})
        if r.status_code != 200: return f"Tavily错误: {r.status_code}"
        d = r.json(); results = d.get("results", [])
        if not results: return f"未找到 '{q}'"
        return f"Tavily搜索 '{q}':\n" + "\n".join(f"{i}. {x.get('title','')}\n   {x.get('content','')[:200]}\n   {x.get('url','')}" for i, x in enumerate(results[:n], 1))

    async def _brave(self, q, n, k):
        c = _get_client(); c.timeout = 10
        r = await c.get("https://api.search.brave.com/res/v1/web/search", params={"q": q, "count": n}, headers={"Accept": "application/json", "X-Subscription-Token": k})
        if r.status_code != 200: return f"Brave错误: {r.status_code}"
        d = r.json(); results = d.get("web", {}).get("results", [])
        if not results: return f"未找到 '{q}'"
        return f"Brave搜索 '{q}':\n" + "\n".join(f"{i}. {x.get('title','')}\n   {x.get('description','')[:200]}\n   {x.get('url','')}" for i, x in enumerate(results[:n], 1))

    async def _ddg(self, q, n):
        from html.parser import HTMLParser
        c = _get_client(); c.timeout = 10
        r = await c.get("https://html.duckduckgo.com/html/", params={"q": q}, headers={"User-Agent": "QQ-Agent/1.0"})
        if r.status_code != 200: return f"搜索失败: HTTP {r.status_code}"

        class P(HTMLParser):
            def __init__(s):
                super().__init__(); s.res = []; s.ir = False; s.ct = ""; s.cs = ""
            def handle_starttag(s, t, a):
                d = dict(a)
                if "result__a" in d.get("class", ""): s.ir = True
                if "result__snippet" in d.get("class", ""): s.ir = True; s.cs = ""
            def handle_data(s, d):
                if s.ir: s.ct += d
            def handle_endtag(s, t):
                if t == "a" and s.ir and s.ct.strip():
                    s.res.append({"title": s.ct.strip(), "snippet": s.cs})
                    s.ct = ""; s.ir = False

        p = P(); p.feed(r.text); results = p.res[:n]
        if not results: return f"未找到 '{q}'"
        return f"DuckDuckGo '{q}':\n" + "\n".join(f"{i}. {x['title']}" for i, x in enumerate(results, 1))


class WebFetchTool(BaseTool):
    name = "web_fetch"
    description = "获取指定网页的内容"
    permission_level = 1

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {"url": {"type": "string", "description": "网页URL"}}, "required": ["url"]}

    async def execute(self, url: str, **kwargs) -> str:
        safe, err = await _is_safe_url(url)
        if not safe: return f"访问被拒绝: {err}"
        try:
            c = _get_client()
            r = await c.get(url, headers={"User-Agent": "QQ-Agent/1.0"})
            if r.status_code != 200: return f"获取失败: HTTP {r.status_code}"
            import re
            t = r.text
            t = re.sub(r'<script[^>]*>.*?</script>', '', t, flags=re.DOTALL | re.IGNORECASE)
            t = re.sub(r'<style[^>]*>.*?</style>', '', t, flags=re.DOTALL | re.IGNORECASE)
            t = re.sub(r'<[^>]+>', ' ', t)
            t = re.sub(r'\s+', ' ', t).strip()
            if len(t) > 4000: t = t[:4000] + "\n... (已截断)"
            return f"网页内容 ({url}):\n\n{t}"
        except Exception as e: return f"获取失败: {e}"
