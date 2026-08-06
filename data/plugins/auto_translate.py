from __future__ import annotations
import aiohttp
from src.adapter.event import AgentEvent
from src.adapter.message import escape_cq
from src.security.auth import is_safe_url

_API = "https://api.mymemory.translated.net/get"

def _detect(text: str) -> str:
    cjk = sum(1 for c in text if '一'<=c<='鿿' or '぀'<=c<='ヿ')
    if cjk > len(text)*0.3: return "zh"
    cyr = sum(1 for c in text if 'Ѐ'<=c<='ӿ')
    if cyr > len(text)*0.3: return "ru"
    lat = sum(1 for c in text if c.isascii() and c.isalpha())
    if lat > len(text)*0.3: return "en"
    return "auto"

def setup(registry) -> None:
    @registry.command("翻译", permission_level=0)
    async def handler(event: AgentEvent):
        text = (event.state.get("command_arg") or "").strip()
        if not text:
            await event.reply("用法: /翻译 <文本>")
            return None
        if not is_safe_url(_API): return None
        lang = _detect(text)
        if lang == "zh":
            await event.reply("已是中文,无需翻译")
            return None
        async with aiohttp.ClientSession() as s:
            try:
                async with s.get(_API, params={"q":text,"langpair":f"{lang}|zh","mt":"1"}, headers={"User-Agent":"QQBot/1.0"}, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    data = await resp.json(content_type=None)
            except Exception:
                await event.reply("翻译服务不可用")
                return None
        if data.get("responseStatus") != 200:
            await event.reply("翻译失败")
            return None
        result = data.get("responseData",{}).get("translatedText","")
        names = {"en":"英语","ja":"日语","ko":"韩语","ru":"俄语","fr":"法语","de":"德语","auto":"自动"}
        label = names.get(lang, lang.upper())
        if result:
            await event.reply(f"[{label}→中文] {escape_cq(result)}")
        else:
            await event.reply("无翻译结果")
        return None
