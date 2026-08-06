from __future__ import annotations
from urllib.parse import quote
import aiohttp
from src.adapter.event import AgentEvent
from src.adapter.message import escape_cq
from src.security.auth import is_safe_url

_session: aiohttp.ClientSession | None = None
BASE = "https://wttr.in"

def setup(registry) -> None:
    @registry.command("天气lite", permission_level=0)
    async def handler(event: AgentEvent):
        global _session
        city = (event.state.get("command_arg") or "").strip()
        if not city:
            await event.reply("用法: /天气lite <城市>")
            return None
        url = f"{BASE}/{quote(city, safe='')}?format=4"
        if not is_safe_url(url):
            await event.reply("查询失败")
            return None
        if _session is None:
            _session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10))
        try:
            async with _session.get(url) as resp:
                if resp.status == 200:
                    result = (await resp.text()).strip()
                    if result:
                        await event.reply(f"{escape_cq(city)}: {escape_cq(result)}")
                    else:
                        await event.reply(f"未找到城市: {escape_cq(city)}")
                else:
                    await event.reply(f"查询失败({resp.status})")
        except Exception:
            await event.reply("天气查询失败,请稍后")
        return None
