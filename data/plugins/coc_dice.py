"""CoC 骰娘:克苏鲁的呼唤 TRPG 掷骰(移植自 NoneBot 社区 cocdicer)。

命令:/coc 属性骰(3d6*5) / /sc <值> 理智检定(1d100) / /rc <值> 成长检定
纯随机逻辑,无状态。
"""
from __future__ import annotations

import random
import re

from src.adapter.event import AgentEvent

_ATTR_RE = re.compile(r"^(\d+)d(\d+)$")


def _roll(expr: str) -> str:
    m = _ATTR_RE.match(expr)
    if not m:
        return ""
    count, sides = int(m.group(1)), int(m.group(2))
    if count < 1 or count > 100 or sides < 1 or sides > 1000:
        return ""
    rolls = [random.randint(1, sides) for _ in range(count)]
    total = sum(rolls)
    detail = "+".join(str(r) for r in rolls)
    return f"{expr}={total}({detail})"


def setup(registry) -> None:
    @registry.command("coc")
    async def coc_attr(event: AgentEvent):
        arg = (event.state.get("command_arg") or "").strip()
        if arg:
            result = _roll(arg)
            if result:
                await event.reply(f"🎲 CoC 掷骰: {result}")
                return None
        # 默认:属性骰 5 次 3d6
        attrs = ["力量", "体质", "体型", "敏捷", "外貌", "智力", "意志", "教育"]
        rolls = [f"3d6*5={sum(random.randint(1, 6) for _ in range(3)) * 5}" for _ in range(5)]
        picked = random.sample(attrs, 5)
        lines = [f"🎲 CoC 属性骰:"]
        for name, roll in zip(picked, rolls):
            lines.append(f"  {name}: {roll}")
        await event.reply("\n".join(lines))
        return None

    @registry.command("sc")
    async def sanity_check(event: AgentEvent):
        arg = (event.state.get("command_arg") or "").strip()
        r = random.randint(1, 100)
        reply = f"🎲 理智检定: 1d100={r}"
        if arg:
            try:
                val = int(arg)
                if r <= val:
                    reply += f" ≤ {val} → 成功 ✅"
                else:
                    reply += f" > {val} → 失败 ❌"
            except ValueError:
                pass
        await event.reply(reply)
        return None

    @registry.command("rc")
    async def recovery_check(event: AgentEvent):
        arg = (event.state.get("command_arg") or "").strip()
        r = random.randint(1, 100)
        reply = f"🎲 成长检定: 1d100={r}"
        if arg:
            try:
                val = int(arg)
                if r > val:
                    plus = random.randint(1, 10)
                    reply += f" > {val} → 成功 +1d10={plus}"
                else:
                    reply += f" ≤ {val} → 失败"
            except ValueError:
                pass
        await event.reply(reply)
        return None
