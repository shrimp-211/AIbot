from __future__ import annotations
import aiohttp
from src.adapter.event import AgentEvent
from src.adapter.message import escape_cq
from src.security.auth import is_safe_url

_API = "https://api.bilibili.com/x/relation/followings"
_HEADERS = {"User-Agent": "Mozilla/5.0"}
_VUP_KW = ["VUP","VTuber","VirtuaReal","虚拟主播","vtuber","vup"]

def _is_vup(card: dict) -> bool:
    desc = (card.get("official_verify") or {}).get("desc", "")
    return any(k.lower() in desc.lower() for k in _VUP_KW)

def setup(registry) -> None:
    @registry.command("查成分", permission_level=0)
    async def handler(event: AgentEvent):
        uid = (event.state.get("command_arg") or "").strip()
        if not uid.isdigit():
            await event.reply("用法: /查成分 <B站UID>")
            return None
        if not is_safe_url(_API): return None
        vups = []
        async with aiohttp.ClientSession() as s:
            for p in (1,2):
                try:
                    async with s.get(_API, params={"vmid":uid,"pn":p,"ps":50,"order":"desc"}, headers=_HEADERS, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                        data = await resp.json(content_type=None)
                except Exception:
                    break
                if data.get("code") != 0: break
                for c in data.get("data",{}).get("list",[]):
                    if _is_vup(c):
                        vups.append(escape_cq(c.get("uname","未知")))
                        if len(vups) >= 50: break
                if len(vups) >= 50: break
        if not vups:
            await event.reply("未找到关注的VUP/VTuber")
        else:
            await event.reply("关注的VUP/VTuber:\n" + "\n".join(f"{i}. {n}" for i,n in enumerate(vups,1)))
        return None
