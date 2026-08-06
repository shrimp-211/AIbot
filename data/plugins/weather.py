"""天气查询插件:免费接口 wttr.in(无需 API Key,支持中文城市)。

命令: /天气 北京 / 天气 Shanghai
城市名经过字符白名单校验,接口域名固定,无 SSRF 风险。
"""
from __future__ import annotations

import asyncio
import re

import aiohttp

from src.adapter.event import AgentEvent
from src.adapter.message import escape_cq

_ENDPOINT = "https://wttr.in/{city}?format=j1&lang=zh"
_CITY_RE = re.compile(r"^[\w一-鿿\s]{1,30}$")


def setup(registry) -> None:
    @registry.command("天气")
    async def weather(event: AgentEvent):
        city = (event.state.get("command_arg") or "").strip()
        if not city or not _CITY_RE.match(city):
            await event.reply("用法: /天气 北京 或 /天气 Shanghai")
            return None
        try:
            timeout = aiohttp.ClientTimeout(total=10)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(_ENDPOINT.format(city=city)) as resp:
                    if resp.status != 200:
                        await event.reply("😥 查不到该城市,换个写法试试?")
                        return None
                    # wttr.in 以 text/plain 返回 JSON,需忽略 content-type
                    data = await resp.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError):
            await event.reply("🌧 天气服务暂时不可用,请稍后再试。")
            return None
        try:
            cur = data["current_condition"][0]
            area = data["nearest_area"][0]["areaName"][0]["value"]
            temp = cur["temp_C"]
            feel = cur["FeelsLikeC"]
            desc = cur.get("lang_zh", [{}])[0].get("value") or cur.get("weatherDesc", [{}])[0].get("value", "")
            humi = cur.get("humidity", "?")
            wind = cur.get("windspeedKmph", "?")
        except (KeyError, IndexError, TypeError):
            await event.reply("😥 天气数据格式异常,请稍后再试。")
            return None
        await event.reply(
            f"🌤 {escape_cq(area)} 当前天气\n温度: {temp}°C (体感 {feel}°C)\n"
            f"天气: {escape_cq(desc)}\n湿度: {humi}% | 风速: {wind} km/h"
        )
        return None
