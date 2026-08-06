from __future__ import annotations
import re, aiohttp
from src.adapter.event import AgentEvent
from src.security.auth import is_safe_url

_API = "https://api.bilibili.com/x/web-interface/view"
_BV_RE = re.compile(r"(?:BV[a-zA-Z0-9]{10}|av\d+)")
_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

def setup(registry) -> None:
    @registry.message(block=False)
    async def handler(event: AgentEvent):
        text = event.plain_text
        m = _BV_RE.search(text)
        if not m: return None
        if not is_safe_url(_API): return None
        vid = m.group(0)
        params = {"bvid": vid} if vid.startswith("BV") else {"aid": vid[2:]}
        async with aiohttp.ClientSession() as s:
            try:
                async with s.get(_API, params=params, headers=_HEADERS, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                    data = await resp.json(content_type=None)
            except Exception:
                return None
        if data.get("code") != 0: return None
        info = data.get("data", {})
        title = info.get("title", "未知")
        up = (info.get("owner") or {}).get("name", "未知")
        views = (info.get("stat") or {}).get("view", 0)
        await event.reply(f"[B站] {title}\nUP: {up} | 播放: {views}")
        return None
