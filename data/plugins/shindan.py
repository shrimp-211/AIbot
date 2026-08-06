from __future__ import annotations
from src.adapter.event import AgentEvent
from src.adapter.message import escape_cq
from src.security.auth import is_safe_url

def setup(registry) -> None:
    @registry.command("占卜", permission_level=0)
    async def handler(event: AgentEvent):
        sid = (event.state.get("command_arg") or "").strip()
        if not sid or not sid.isdigit():
            await event.reply("用法: /占卜 <id>\n前往 shindanmaker.com 输入名字查看占卜")
            return None
        url = f"https://shindanmaker.com/{sid}"
        if not is_safe_url(url):
            await event.reply("无效占卜ID")
            return None
        await event.reply(f"占卜 #{escape_cq(sid)}\n前往 {escape_cq(url)} 输入名字查看结果")
        return None
