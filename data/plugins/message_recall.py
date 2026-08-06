from __future__ import annotations
from src.adapter import AdapterRegistry
from src.adapter.event import AgentEvent


def setup(registry) -> None:
    @registry.command("撤回", permission_level=4)
    async def handler(event: AgentEvent, adapter_registry: AdapterRegistry):
        adapter = adapter_registry.get("qq")
        if adapter is None:
            await event.reply("当前无可用适配器")
            return None
        msg_id = await adapter.recent_bot_message(event.session_id)
        if msg_id is None:
            await event.reply("没有可撤回的消息")
            return None
        delete = getattr(adapter, "delete_msg", None)
        if not callable(delete):
            await event.reply("当前适配器不支持撤回")
            return None
        try:
            await delete(msg_id)
        except Exception:
            await event.reply("撤回失败,可能已超过撤回时限")
            return None
        forget = getattr(adapter, "forget_bot_message", None)
        if callable(forget):
            await forget(event.session_id)
        await event.reply("已撤回")
        return None
