"""关键词自动回复插件:管理员配置 关键词→回复,命中自动应答。

经典 NoneBot 群管/客服插件。管理命令需管理员权限(ADMIN=4)。
命令:
- /关键词 添加 <关键词> <回复内容>
- /关键词 删除 <关键词>
- /关键词 列表
- /关键词 清除
数据持久化到 JsonKV(key: plugin:keyword_reply)。
"""
from __future__ import annotations

from src.adapter.event import AgentEvent
from src.storage.db import JsonKV

_KEY = "plugin:keyword_reply"
_ADMIN_LEVEL = 4

_HELP = (
    "📖 关键词回复管理(需管理员)\n"
    "/关键词 添加 <关键词> <回复>\n"
    "/关键词 删除 <关键词>\n"
    "/关键词 列表\n"
    "/关键词 清除"
)


def _load(db: JsonKV) -> dict[str, str]:
    return db.get(_KEY) or {}


def _save(db: JsonKV, rules: dict[str, str]) -> None:
    db.set(_KEY, rules)


def setup(registry) -> None:
    @registry.message(priority=90, block=False)
    async def auto_reply(event: AgentEvent, db: JsonKV):
        """命中关键词自动回复;未命中返回 None 交后续处理。"""
        rules = _load(db)
        if not rules:
            return None
        text = event.plain_text.strip()
        for kw, reply in rules.items():
            if kw and kw in text:
                await event.reply(reply)
                return True  # 已回复,阻断后续 agent
        return None

    @registry.command("关键词", permission_level=_ADMIN_LEVEL)
    async def manage(event: AgentEvent, db: JsonKV):
        arg = (event.state.get("command_arg") or "").strip()
        if not arg:
            await event.reply(_HELP)
            return None
        parts = arg.split(None, 2)
        action = parts[0]
        rest = parts[1] if len(parts) > 1 else ""
        extra = parts[2] if len(parts) > 2 else ""
        rules = _load(db)

        if action == "列表":
            if not rules:
                await event.reply("还没有配置任何关键词。")
                return None
            lines = [f"📋 关键词回复({len(rules)} 条)"]
            for kw, reply in rules.items():
                lines.append(f"· {kw} → {reply[:24]}")
            await event.reply("\n".join(lines))
        elif action == "添加":
            if not rest or not extra:
                await event.reply("用法: /关键词 添加 <关键词> <回复内容>")
                return None
            rules[rest] = extra
            _save(db, rules)
            await event.reply(f"✅ 已添加: {rest} → {extra[:30]}")
        elif action == "删除":
            if not rest:
                await event.reply("用法: /关键词 删除 <关键词>")
                return None
            if rules.pop(rest, None) is None:
                await event.reply(f"未找到关键词: {rest}")
            else:
                _save(db, rules)
                await event.reply(f"🗑 已删除关键词: {rest}")
        elif action == "清除":
            _save(db, {})
            await event.reply("🗑 已清空全部关键词回复。")
        else:
            await event.reply(_HELP)
        return None
