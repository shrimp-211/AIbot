"""互动工具插件:点赞 / 加精华(经典 QQ 机器人功能)。

命令:
- /赞 <@成员> [次数]   给成员点赞,次数默认 1,上限 5
- /精华 <消息id>       将消息设为精华(需管理员),也支持回复消息后直接发 /精华
"""
from __future__ import annotations

from src.adapter import AdapterRegistry, BaseAdapter
from src.adapter.event import AgentEvent
from src.adapter.message import escape_cq

_MAX_LIKE = 5
_ADMIN_LEVEL = 4


def _target_qq(event: AgentEvent) -> str | None:
    ats = event.message.get("at")
    if not ats:
        return None
    qq = str(ats[0].data.get("qq", "") or "").strip()
    return qq or None


def _like_times(arg: str) -> int | None:
    """解析点赞次数;非法时返回 None。"""
    for token in arg.split():
        if token.isdigit():
            n = int(token)
            if not 1 <= n <= _MAX_LIKE:
                return None
            return n
    return 1


def _reply_message_id(event: AgentEvent) -> str | None:
    replies = event.message.get("reply")
    if not replies:
        return None
    mid = str(replies[0].data.get("id", "") or "").strip()
    return mid or None


async def _adapter(registry: AdapterRegistry, event: AgentEvent) -> BaseAdapter | None:
    adapter = registry.get("qq") or (registry.all()[0] if registry.all() else None)
    if adapter is None:
        await event.reply("当前没有可用的 QQ 适配器。")
    return adapter


async def _nickname(adapter: BaseAdapter, qq: str) -> str:
    """尝试获取昵称,失败时回退为 QQ 号。"""
    try:
        info = await adapter.get_stranger_info(qq)
        nick = (info or {}).get("nickname") or ""
        return str(nick).strip() or qq
    except Exception:  # noqa: BLE001
        return qq


def setup(registry) -> None:
    @registry.command("赞", permission_level=0)
    async def like(event: AgentEvent, registry: AdapterRegistry):
        qq = _target_qq(event)
        if not qq:
            await event.reply("用法: /赞 <@成员> [次数](上限 5)")
            return None
        times = _like_times((event.state.get("command_arg") or "").strip())
        if times is None:
            await event.reply(f"次数需为 1-{_MAX_LIKE} 之间的整数。")
            return None
        adapter = await _adapter(registry, event)
        if adapter is None:
            return None
        try:
            await adapter.send_like(qq, times=times)
        except Exception:  # noqa: BLE001
            await event.reply("点赞失败,可能对方不是好友或频率受限。")
            return None
        nick = await _nickname(adapter, qq)
        await event.reply(f"👍 已给 {escape_cq(nick)} 点了 {times} 个赞。")
        return None

    @registry.command("精华", permission_level=_ADMIN_LEVEL)
    async def essence(event: AgentEvent, registry: AdapterRegistry):
        mid = (event.state.get("command_arg") or "").strip() or _reply_message_id(event)
        if not mid:
            await event.reply("用法: /精华 <消息id>,或回复要加精华的消息后发送 /精华")
            return None
        if not mid.isdigit():
            await event.reply("消息 id 无效,请提供正确的数字消息 id。")
            return None
        adapter = await _adapter(registry, event)
        if adapter is None:
            return None
        try:
            await adapter.set_essence_msg(int(mid))
        except Exception:  # noqa: BLE001
            await event.reply("设置精华失败,可能消息不存在、已在精华或权限不足。")
            return None
        await event.reply("📌 已设为精华。")
        return None
