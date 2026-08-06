from __future__ import annotations
import asyncio, time
from src.adapter.event import AgentEvent
from src.adapter.message import escape_cq

_reminders: dict[int, asyncio.Task] = {}
_info: dict[int, tuple[float, float, str, str]] = {}
_id = 0

async def _delayed(event: AgentEvent, text: str, delay: float):
    try: await asyncio.sleep(delay); await event.reply(f"[提醒] {escape_cq(text)}")
    except asyncio.CancelledError: pass

def setup(registry) -> None:
    @registry.command("提醒", permission_level=0)
    async def handler(event: AgentEvent):
        global _id
        arg = (event.state.get("command_arg") or "").strip()
        if not arg:
            await event.reply("用法: /提醒 <分钟后> <内容>\n/提醒列表")
            return None
        if arg == "列表":
            if not _info:
                await event.reply("暂无进行中的提醒")
                return None
            lines = ["进行中的提醒:"]
            for rid, (st, dm, sid, txt) in _info.items():
                remain = max(0, dm-(time.time()-st)/60)
                lines.append(f"  ID:{rid} | {remain:.0f}分钟后 | {escape_cq(txt)[:30]}")
            await event.reply("\n".join(lines))
            return None
        parts = arg.split(None, 1)
        if len(parts) < 2:
            await event.reply("用法: /提醒 <分钟后> <内容>")
            return None
        try:
            mins = float(parts[0])
        except ValueError:
            await event.reply("分钟后必须为数字")
            return None
        if mins <= 0 or mins > 1440:
            await event.reply("提醒时间需在 1-1440 分钟之间")
            return None
        txt = parts[1]
        _id += 1; rid = _id
        task = asyncio.create_task(_delayed(event, txt, mins*60))
        _reminders[rid] = task
        _info[rid] = (time.time(), mins, event.session_id, txt)
        def _done(t, r=rid): _reminders.pop(r,None); _info.pop(r,None)
        task.add_done_callback(_done)
        await event.reply(f"已设置提醒 ID:{rid}, {mins:.0f} 分钟后")
        return None
