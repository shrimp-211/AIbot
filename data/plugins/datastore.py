from __future__ import annotations
from src.adapter.event import AgentEvent
from src.adapter.message import escape_cq
from src.storage.db import JsonKV

def _prefix(event: AgentEvent, key: str) -> str:
    gid = getattr(event, "group_id", None)
    return f"g_{gid}:{key}" if gid else f"u_{event.user_id}:{key}"

def setup(registry) -> None:
    @registry.command("存", permission_level=0)
    async def store(event: AgentEvent, db: JsonKV):
        arg = (event.state.get("command_arg") or "").strip()
        parts = arg.split(None, 1)
        if len(parts) < 2:
            await event.reply("用法: /存 <key> <value>")
            return None
        db.set(_prefix(event, parts[0]), parts[1])
        await event.reply(f"已存 {escape_cq(parts[0])}")
        return None

    @registry.command("取", permission_level=0)
    async def get_val(event: AgentEvent, db: JsonKV):
        key = (event.state.get("command_arg") or "").strip()
        if not key:
            await event.reply("用法: /取 <key>")
            return None
        val = db.get(_prefix(event, key))
        await event.reply(f"{escape_cq(key)} = {escape_cq(str(val))}" if val is not None else f"键 {escape_cq(key)} 不存在")
        return None

    @registry.command("删", permission_level=0)
    async def del_val(event: AgentEvent, db: JsonKV):
        key = (event.state.get("command_arg") or "").strip()
        if not key:
            await event.reply("用法: /删 <key>")
            return None
        full = _prefix(event, key)
        if db.get(full) is not None:
            db.set(full, None)
            await event.reply(f"已删 {escape_cq(key)}")
        else:
            await event.reply(f"键 {escape_cq(key)} 不存在")
        return None
