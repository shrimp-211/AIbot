"""记账本插件:支出记录 / 查账 / 删账 / 当日合计。

经典 QQ 机器人「记账本」功能移植。
命令:
- /记账 <金额> [备注]   记录一笔支出,回复今日累计
- /查账                列出最近 10 笔支出与总支出
- /删账 <序号>         按 /查账 显示序号删除一笔
- /本日支出            今日支出合计
"""
from __future__ import annotations

import datetime
import math

from src.adapter.event import AgentEvent
from src.adapter.message import escape_cq
from src.storage.db import JsonKV

_KEY = "plugin:ledger"


def _load(db: JsonKV) -> dict:
    data = db.get(_KEY) or {}
    data.setdefault("users", {})
    return data


def _save(db: JsonKV, data: dict) -> None:
    db.set(_KEY, data)


def _fmt_amount(v: float) -> str:
    return f"{v:.2f}"


def _fmt_time(ts: float) -> str:
    return datetime.datetime.fromtimestamp(ts).strftime("%m-%d %H:%M")


def _today_items(items: list[dict]) -> list[dict]:
    today = datetime.date.today().toordinal()
    return [it for it in items if datetime.datetime.fromtimestamp(it["ts"]).toordinal() == today]


def _parse_amount(s: str) -> float | None:
    try:
        v = float(s)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(v) or v <= 0:
        return None
    return v


def setup(registry) -> None:
    @registry.command("记账")
    async def add_expense(event: AgentEvent, db: JsonKV):
        arg = (event.state.get("command_arg") or "").strip()
        if not arg:
            await event.reply("用法: /记账 <金额> [备注] 例: /记账 25 午饭")
            return None
        parts = arg.split(maxsplit=1)
        amount = _parse_amount(parts[0])
        if amount is None:
            await event.reply("金额需为正数数字,请重新输入。")
            return None
        note = parts[1].strip()[:50] if len(parts) > 1 else ""
        data = _load(db)
        users = data["users"]
        uid = event.user_id
        items = users.get(uid, {}).get("items") or []
        items.append({"ts": datetime.datetime.now().timestamp(), "amount": amount, "note": note})
        users[uid] = {"items": items}
        _save(db, data)
        today_total = sum(it["amount"] for it in _today_items(items))
        reply = f"✅ 已记账:{escape_cq(note) or '无备注'} -{_fmt_amount(amount)} 元"
        reply += f"\n今日累计支出: {_fmt_amount(today_total)} 元"
        await event.reply(reply)
        return None

    @registry.command("查账")
    async def query(event: AgentEvent, db: JsonKV):
        items = _load(db)["users"].get(event.user_id, {}).get("items") or []
        if not items:
            await event.reply("还没有记账记录,发送「记账」开始记一笔吧~")
            return None
        recent = sorted(items, key=lambda it: it["ts"], reverse=True)[:10]
        total = sum(it["amount"] for it in items)
        lines = [f"📒 最近 {len(recent)} 笔支出 | 总支出: {_fmt_amount(total)} 元"]
        for i, it in enumerate(recent, 1):
            note = escape_cq(it["note"]) or "(无备注)"
            lines.append(f"{i}. {_fmt_time(it['ts'])} -{_fmt_amount(it['amount'])} 元 {note}")
        await event.reply("\n".join(lines))
        return None

    @registry.command("删账")
    async def delete_expense(event: AgentEvent, db: JsonKV):
        arg = (event.state.get("command_arg") or "").strip()
        if not arg:
            await event.reply("用法: /删账 <序号> 例: /删账 3")
            return None
        try:
            idx = int(arg)
        except ValueError:
            await event.reply("序号需为数字。")
            return None
        if idx < 1:
            await event.reply("序号需为正整数。")
            return None
        data = _load(db)
        items = data["users"].get(event.user_id, {}).get("items") or []
        if not items:
            await event.reply("还没有可删除的记账记录。")
            return None
        recent = sorted(items, key=lambda it: it["ts"], reverse=True)
        if idx > len(recent):
            await event.reply(f"序号超出范围,当前共 {len(recent)} 笔。")
            return None
        target = recent[idx - 1]
        for i, it in enumerate(items):
            if it is target:
                items.pop(i)
                break
        _save(db, data)
        await event.reply(f"🗑 已删除:{_fmt_time(target['ts'])} -{_fmt_amount(target['amount'])} 元")
        return None

    @registry.command("本日支出")
    async def today_total(event: AgentEvent, db: JsonKV):
        items = _load(db)["users"].get(event.user_id, {}).get("items") or []
        todays = _today_items(items)
        total = sum(it["amount"] for it in todays)
        date_str = datetime.date.today().strftime("%m-%d")
        await event.reply(f"📅 {date_str} 支出: {_fmt_amount(total)} 元 ({len(todays)} 笔)")
        return None
