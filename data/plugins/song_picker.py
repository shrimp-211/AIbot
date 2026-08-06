from __future__ import annotations
import aiohttp
from src.adapter.event import AgentEvent
from src.adapter.message import escape_cq
from src.security.auth import is_safe_url

_API = "https://music.163.com/api/search/get"
_HEADERS = {"User-Agent":"Mozilla/5.0","Referer":"https://music.163.com/"}

def setup(registry) -> None:
    @registry.command("点歌", permission_level=0)
    async def handler(event: AgentEvent):
        song = (event.state.get("command_arg") or "").strip()
        if not song:
            await event.reply("用法: /点歌 <歌名>")
            return None
        if not is_safe_url(_API): return None
        async with aiohttp.ClientSession() as s:
            try:
                async with s.post(_API, data={"type":1,"s":song,"limit":3,"offset":0}, headers=_HEADERS, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    data = await resp.json(content_type=None)
            except Exception:
                await event.reply("搜索失败")
                return None
        if data.get("code") != 200:
            await event.reply("搜索失败")
            return None
        songs = data.get("result",{}).get("songs",[])
        if not songs:
            await event.reply(f"未找到「{escape_cq(song)}」")
            return None
        lines = ["搜索结果:"]
        for i, sng in enumerate(songs[:3], 1):
            name = escape_cq(sng.get("name","未知"))
            artists = escape_cq(", ".join(a.get("name","?") for a in sng.get("artists",[])))
            lines.append(f"{i}. {name} - {artists}")
        await event.reply("\n".join(lines))
        return None
