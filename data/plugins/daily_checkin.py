"""每日签到插件:积分 + 连续签到 + 排行(数据持久化到 JsonKV)。

经典 QQ 机器人「签到」功能,参考 NoneBot2 生态同类插件实现。
命令:
- /签到      今日签到,积分 = 基础 10 + 连续加成
- /我的积分  查询个人积分与连续天数
- /签到排行  查看积分 Top10
"""
from __future__ import annotations

import datetime

from src.adapter.event import AgentEvent
from src.storage.db import JsonKV

_KEY = "plugin:checkin"
_BASE = 10
_STREAK_BONUS = 2


def _load(db: JsonKV) -> dict:
    data = db.get(_KEY) or {}
    data.setdefault("users", {})
    return data


def _save(db: JsonKV, data: dict) -> None:
    db.set(_KEY, data)


def setup(registry) -> None:
    @registry.command("签到")
    async def checkin(event: AgentEvent, db: JsonKV):
        today = datetime.date.today().isoformat()
        yesterday = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
        data = _load(db)
        users = data["users"]
        uid = event.user_id
        rec = users.get(uid) or {"points": 0, "streak": 0, "last": "", "name": event.sender_name}
        if rec.get("last") == today:
            await event.reply(f"今天已经签到过了哦~ 当前积分: {rec['points']}")
            return None
        # 昨天签过 → 连续天数累加;否则重置为 1
        rec["streak"] = rec["streak"] + 1 if rec.get("last") == yesterday else 1
        bonus = min(rec["streak"] - 1, 10) * _STREAK_BONUS
        rec["points"] += _BASE + bonus
        rec["last"] = today
        rec["name"] = event.sender_name or rec.get("name") or uid
        users[uid] = rec
        _save(db, data)
        await event.reply(
            f"✅ 签到成功!\n积分 +{_BASE + bonus} (含连续加成 +{bonus})\n"
            f"连续签到: {rec['streak']} 天 | 当前积分: {rec['points']}"
        )
        return None

    @registry.command("我的积分")
    async def my_points(event: AgentEvent, db: JsonKV):
        rec = _load(db)["users"].get(event.user_id)
        if not rec:
            await event.reply("还没有签到记录,发送「签到」开始积累积分吧~")
            return None
        await event.reply(
            f"💰 积分: {rec['points']}\n连续签到: {rec['streak']} 天\n上次签到: {rec['last']}"
        )
        return None

    @registry.command("签到排行")
    async def rank(event: AgentEvent, db: JsonKV):
        users = _load(db)["users"]
        if not users:
            await event.reply("还没有人签到过,快来当第一名吧!")
            return None
        top = sorted(users.items(), key=lambda kv: kv[1]["points"], reverse=True)[:10]
        medals = {1: "🥇", 2: "🥈", 3: "🥉"}
        lines = [f"🏆 签到积分排行 Top {len(top)}"]
        for i, (uid, rec) in enumerate(top, 1):
            medal = medals.get(i, "·")
            name = rec.get("name") or uid
            lines.append(f"{medal} {i}. {name}: {rec['points']} 分")
        await event.reply("\n".join(lines))
        return None
