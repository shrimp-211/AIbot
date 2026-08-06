"""疯狂星期四:KFC 疯狂星期四文案(移植自 NoneBot 社区插件)。

触发:"疯狂星期X"(X=一二三四五六日)或"狂乱X曜日"(X=月火水木金土日)。
数据源:post.json 嵌入为常量。
"""
from __future__ import annotations

import random

from src.adapter.event import AgentEvent
from src.adapter.message import escape_cq

_KFC_POSTS = [
    "今天是疯狂星期四!! 转发这个 KFC 原味鸡,你就能获得一个原味鸡。我试过了,是假的,但今天真的是疯狂星期四。",
    "大家好,我是肯德基的厨师。今天是疯狂星期四,由于餐厅食材短缺,我们不得不暂停营业。但是我们有一个好消息:凡在今天之前在本店消费过的顾客,凭小票可以享受免费全家桶一份。",
    "我决定永远不理会那些说我胖的人,因为今天是疯狂星期四,我要吃一份全家桶。",
    "疯狂星期四,感恩回馈,转发本条消息到 5 个群,即可免费获得原味鸡一块。别问我怎么知道,问就是我是鸡。",
    "大家好,我是肯德基的收银员。今天的疯狂星期四活动是:购买任意套餐 +9.9 元换购原味鸡两块。欢迎大家前来品尝!",
    "各位家人,今天是肯德基疯狂星期四,由于原料短缺,今天的炸鸡用的是隔壁华莱士的鸡,请大家谅解。作为补偿,全场半价。",
    "V 我 50,请你吃疯狂星期四。",
    "今天疯狂星期四,谁请我吃?",
    "疯狂星期四,转发这条消息到 5 个群,肯德基会送你一份全家桶。当然,这是假的,但万一是真的呢?",
    "都说了今天疯狂星期四,怎么还没人 V 我 50?",
]

# jp→cn
_JP_CN: dict[str, str] = {"月": "一", "火": "二", "水": "三", "木": "四", "金": "五", "土": "六", "日": "日"}
_CN_JP: dict[str, str] = {"一": "月", "二": "火", "三": "水", "四": "木", "五": "金", "六": "土", "日": "日"}
_CN_EN: dict[str, str] = {"一": "Monday", "二": "Tuesday", "三": "Wednesday", "四": "Thursday", "五": "Friday", "六": "Saturday", "日": "Sunday"}


def setup(registry) -> None:
    @registry.regex(r"^疯狂星期\S$")
    async def crazy_cn(event: AgentEvent):
        day = event.plain_text.strip()[-1]
        if day == "天":
            day = "日"
        await event.reply(_random_kfc_cn(day))
        return None

    @registry.regex(r"^狂乱\S曜日$")
    async def crazy_jp(event: AgentEvent):
        jp_day = event.plain_text.strip()[-3]
        day = _JP_CN.get(jp_day)
        if day:
            await event.reply(_random_kfc_cn(day))
        return None


def _random_kfc_cn(day: str) -> str:
    if day not in _CN_JP:
        return "给个准时间,OK?"
    text = random.choice(_KFC_POSTS)
    jp = _CN_JP[day]
    en = _CN_EN[day]
    cn_char = day
    text = text.replace("木曜日", f"{jp}曜日")
    text = text.replace("Thursday", en)
    text = text.replace("thursday", en.lower())
    text = text.replace("星期四", f"星期{cn_char}")
    text = text.replace("周四", f"周{cn_char}")
    return escape_cq(text)
