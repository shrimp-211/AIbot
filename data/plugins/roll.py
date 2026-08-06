"""掷骰子 / 抽签娱乐插件(纯随机,无网络依赖)。

命令:
- /抽签       今日运势签
- /roll       掷 1d6
- /roll 2d6   掷 2 个六面骰(支持 N d M+修饰)
"""
from __future__ import annotations

import random
import re

from src.adapter.event import AgentEvent
from src.adapter.message import escape_cq

_FORTUNE = [
    ("大吉", "🌟 今天运气爆棚,做什么都顺利!"),
    ("中吉", "✨ 运势不错,把握机会~"),
    ("小吉", "🌤 平稳向好,小有惊喜。"),
    ("吉", "🍀 一切顺利,安心前行。"),
    ("末吉", "🌥 差强人意,稳扎稳打。"),
    ("凶", "🌧 谨慎行事,避免冲动。"),
    ("大凶", "⛈ 保持低调,明日再来。"),
]

_ROLL_RE = re.compile(r"(\d*)d(\d+)(?:\s*\+\s*(\d+))?")


def setup(registry) -> None:
    @registry.command("抽签")
    async def fortune(event: AgentEvent):
        level, desc = random.choice(_FORTUNE)
        name = event.sender_name or event.user_id
        await event.reply(f"🎴 {escape_cq(name)} 的今日签:【{level}】\n{desc}")
        return None

    @registry.command("roll")
    async def roll(event: AgentEvent):
        arg = (event.state.get("command_arg") or "").strip()
        m = _ROLL_RE.fullmatch(arg) if arg else None
        if not m:
            await event.reply("用法: /roll 或 /roll 2d6 或 /roll 1d100+5")
            return None
        count = int(m.group(1) or 1)
        sides = int(m.group(2))
        mod = int(m.group(3) or 0)
        if not 1 <= count <= 100 or not 1 <= sides <= 1000:
            await event.reply("骰子数量需在 1-100,面数 1-1000 之间。")
            return None
        rolls = [random.randint(1, sides) for _ in range(count)]
        total = sum(rolls) + mod
        reply = f"🎲 {count}d{sides}"
        if mod:
            reply += f"+{mod}"
        reply += f" = {total}"
        if count > 1:
            detail = " + ".join(map(str, rolls))
            reply += f"\n明细: ({detail})"
        await event.reply(reply)
        return None
