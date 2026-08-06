from __future__ import annotations
import aiohttp
from src.adapter.event import AgentEvent
from src.adapter.message import escape_cq
from src.security.auth import is_safe_url

_API = "https://zh.wikipedia.org/w/api.php"
_HEADERS = {"User-Agent": "QQBot/1.0"}

def setup(registry) -> None:
    @registry.command("wiki", permission_level=0)
    async def handler(event: AgentEvent):
        kw = (event.state.get("command_arg") or "").strip()
        if not kw:
            await event.reply("用法: /wiki <关键词>")
            return None
        if not is_safe_url(_API): return None
        async with aiohttp.ClientSession() as s:
            try:
                async with s.get(_API, params={"action":"query","list":"search","srsearch":kw,"format":"json","srlimit":1}, headers=_HEADERS, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    data = await resp.json(content_type=None)
            except Exception:
                await event.reply("查询失败")
                return None
        results = data.get("query",{}).get("search",[])
        if not results:
            await event.reply(f"未找到「{escape_cq(kw)}」")
            return None
        title = results[0]["title"]
        async with aiohttp.ClientSession() as s:
            try:
                async with s.get(_API, params={"action":"query","prop":"extracts","exintro":1,"explaintext":1,"titles":title,"format":"json"}, headers=_HEADERS, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    edata = await resp.json(content_type=None)
            except Exception:
                await event.reply("获取摘要失败")
                return None
        pages = edata.get("query",{}).get("pages",{})
        extract = ""
        for pi in pages.values(): extract = pi.get("extract",""); break
        if not extract:
            await event.reply(f"「{escape_cq(title)}」暂无摘要")
            return None
        if len(extract) > 300: extract = extract[:300]+"..."
        await event.reply(f"【{escape_cq(title)}】\n{escape_cq(extract)}")
        return None
