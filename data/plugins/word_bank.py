"""问答库:学习/回复问答(移植自 NoneBot 社区 word_bank2)。

命令:/问 <问题> <回答> 学一句;发"<问题>"匹配答案回复。
用 JsonKV 持久化。模糊匹配:消息在已学问题中精确匹配则回复。
"""
from __future__ import annotations

import re

from src.adapter.event import AgentEvent
from src.adapter.message import escape_cq
from src.storage.db import JsonKV

_KEY = "plugin:word_bank"


def setup(registry) -> None:
    @registry.command("问", permission_level=1)
    async def learn(event: AgentEvent, db: JsonKV):
        arg = (event.state.get("command_arg") or "").strip()
        if not arg:
            await event.reply("用法: /问 <问题> <回答>")
            return None
        m = re.match(r"(.+?)\s+(.+)", arg)
        if not m:
            await event.reply("格式: /问 问题 回答(问题和回答用空格分开)")
            return None
        q, a = m.group(1).strip(), m.group(2).strip()
        bank = db.get(_KEY) or {}
        bank[q] = a
        db.set(_KEY, bank)
        await event.reply(f"已学会: {escape_cq(q)} → {escape_cq(a)}")
        return None

    @registry.command("删问", permission_level=4)
    async def unlearn(event: AgentEvent, db: JsonKV):
        q = (event.state.get("command_arg") or "").strip()
        if not q:
            await event.reply("用法: /删问 <问题>")
            return None
        bank = db.get(_KEY) or {}
        if q in bank:
            del bank[q]
            db.set(_KEY, bank)
            await event.reply(f"已忘记: {escape_cq(q)}")
        else:
            await event.reply("没学过这个问题。")
        return None

    @registry.regex(r"^[^/!].+")  # 非命令消息尝试匹配问答
    async def match_bank(event: AgentEvent, db: JsonKV):
        text = event.plain_text.strip()
        bank = db.get(_KEY) or {}
        if text in bank:
            await event.reply(escape_cq(bank[text]))
        return None
