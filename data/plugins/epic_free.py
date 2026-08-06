from __future__ import annotations
import aiohttp, datetime
from src.adapter.event import AgentEvent
from src.adapter.message import escape_cq
from src.security.auth import is_safe_url

_API = "https://store-site-backend-static.ak.epicgames.com/freeGamesPromotions"

def setup(registry) -> None:
    @registry.command("epic", permission_level=0)
    async def handler(event: AgentEvent):
        if not is_safe_url(_API): return None
        async with aiohttp.ClientSession() as s:
            try:
                async with s.get(_API, params={"locale":"zh-CN","country":"CN","allowCountries":"CN"}, headers={"User-Agent":"Mozilla/5.0"}, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    data = await resp.json(content_type=None)
            except Exception:
                await event.reply("获取失败,稍后重试")
                return None
        now = datetime.datetime.now(datetime.timezone.utc)  # aware,与 fromisoformat 一致
        games = []
        for item in data.get("data",{}).get("Catalog",{}).get("searchStore",{}).get("elements",[]):
            promos = (item.get("promotions") or {}).get("promotionalOffers", [])
            for promo in promos:
                for offer in promo.get("promotionalOffers",[]):
                    try:
                        sd = datetime.datetime.fromisoformat(offer["startDate"].replace("Z","+00:00"))
                        ed = datetime.datetime.fromisoformat(offer["endDate"].replace("Z","+00:00"))
                    except Exception:
                        continue
                    if sd <= now <= ed:
                        title = escape_cq(item.get("title","未知"))
                        slug = item.get("productSlug","")
                        # slug 来自外部 API,拼接进链接前需转义,防 CQ 注入
                        link = f"https://store.epicgames.com/zh-CN/p/{escape_cq(slug)}" if slug else ""
                        games.append(f"{title}\n{link}")
                        break
        if not games:
            await event.reply("当前无Epic免费游戏")
        else:
            await event.reply("本周Epic免费:\n\n"+"\n---\n".join(games))
        return None
