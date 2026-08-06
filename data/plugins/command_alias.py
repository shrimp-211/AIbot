"""命令别名:管理机器人命令别名(移植自 NoneBot 社区 plugin-alias)。

命令:/别名 添加 <别名> <原命令> / /别名 删除 <别名> / /别名 列表
用 JsonKV 持久化。别名仅记录参考,不拦截实时消息(需配合命令系统扩展)。
"""
from __future__ import annotations

from src.adapter.event import AgentEvent
from src.adapter.message import escape_cq
from src.storage.db import JsonKV

_KEY = "plugin:command_alias"


def _load(db: JsonKV) -> dict[str, str]:
    return db.get(_KEY) or {}


def _save(db: JsonKV, data: dict[str, str]) -> None:
    db.set(_KEY, data)


def setup(registry) -> None:
    @registry.command("别名")
    async def alias_cmd(event: AgentEvent, db: JsonKV):
        arg = (event.state.get("command_arg") or "").strip()
        parts = arg.split(maxsplit=1)
        sub = parts[0].lower() if parts else ""
        if sub in ("添加", "add"):
            rest = parts[1] if len(parts) > 1 else ""
            sp = rest.split(maxsplit=1)
            if len(sp) != 2:
                await event.reply("格式: /别名 添加 <别名> <原命令>")
                return None
            aliases = _load(db)
            aliases[sp[0]] = sp[1]
            _save(db, aliases)
            await event.reply(f"已添加别名: {escape_cq(sp[0])} → {escape_cq(sp[1])}")
        elif sub in ("删除", "del"):
            target = parts[1] if len(parts) > 1 else ""
            aliases = _load(db)
            if target in aliases:
                del aliases[target]
                _save(db, aliases)
                await event.reply(f"已删除别名: {escape_cq(target)}")
            else:
                await event.reply("未找到该别名。")
        elif sub in ("列表", "list"):
            aliases = _load(db)
            if not aliases:
                await event.reply("暂无别名。")
            else:
                lines = [f"📋 命令别名 ({len(aliases)} 个):"]
                for a, c in sorted(aliases.items()):
                    lines.append(f"  {escape_cq(a)} → {escape_cq(c)}")
                await event.reply("\n".join(lines))
        else:
            await event.reply("用法:\n/别名 添加 <别名> <原命令>\n/别名 删除 <别名>\n/别名 列表")
        return None
