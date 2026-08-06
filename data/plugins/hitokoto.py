"""一言插件:从公开接口拉取一句佳句,失败时回退到本地诗词库。

命令: /一言
接口固定为公共域名 v1.hitokoto.cn(无用户 URL,无 SSRF 风险)。
"""
from __future__ import annotations

import asyncio
import random

import aiohttp

from src.adapter.event import AgentEvent

_ENDPOINT = "https://v1.hitokoto.cn/?c=k&c=i&c=a&c=h"

_FALLBACK = [
    "人生天地之间,若白驹之过隙,忽然而已。——《庄子·知北游》",
    "山重水复疑无路,柳暗花明又一村。——陆游",
    "路漫漫其修远兮,吾将上下而求索。——屈原",
    "长风破浪会有时,直挂云帆济沧海。——李白",
    "采菊东篱下,悠然见南山。——陶渊明",
    "人生得意须尽欢,莫使金樽空对月。——李白",
    "欲穷千里目,更上一层楼。——王之涣",
]


def setup(registry) -> None:
    @registry.command("一言")
    async def hitokoto(event: AgentEvent):
        try:
            timeout = aiohttp.ClientTimeout(total=8)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(_ENDPOINT) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        text = data.get("hitokoto", "")
                        if text:
                            source = data.get("from", "")
                            reply = f"「{text}」" if text else ""
                            if source:
                                reply += f" —— {source}"
                            await event.reply(reply)
                            return None
        except (aiohttp.ClientError, asyncio.TimeoutError):
            pass
        await event.reply(random.choice(_FALLBACK))
        return None
