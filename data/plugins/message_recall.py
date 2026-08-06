from __future__ import annotations
from src.adapter.event import AgentEvent

_last_bot_msg: dict[str, int] = {}

def _conv_key(event: AgentEvent) -> str:
    gid = getattr(event, "group_id", None)
    return f"g_{gid}" if gid else f"u_{event.user_id}"

def setup(registry) -> None:
    @registry.command("撤回", permission_level=4)
    async def handler(event: AgentEvent):
        from src.adapter import AdapterRegistry
        # 从已注册依赖获取适配器;如未注入则无法工作
        k = _conv_key(event)
        msg_id = _last_bot_msg.pop(k, None)
        if msg_id is None:
            await event.reply("没有可撤回的消息")
            return None
        await event.reply(f"撤回功能需要 AdapterRegistry 支持,消息ID: {msg_id}")
        return None
