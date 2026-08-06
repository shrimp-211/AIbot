"""群管理插件:禁言/解禁/踢人/设管理/全体禁言(需管理员权限)。

通过依赖注入拿到 AdapterRegistry,调用 OneBot 平台 API 执行群管操作。
参考 nonebot 群管插件。命令:
- /禁言 <@成员> [分钟]     默认 10 分钟
- /解禁 <@成员>
- /踢人 <@成员>
- /设管理 <@成员> | /取消管理 <@成员>
- /全体禁言 | /全体解除禁言
"""
from __future__ import annotations

from src.adapter import AdapterRegistry, BaseAdapter
from src.adapter.event import AgentEvent

_ADMIN_LEVEL = 4
_HELP = (
    "🛠 群管理(需管理员)\n"
    "/禁言 <@成员> [分钟]\n"
    "/解禁 <@成员>\n"
    "/踢人 <@成员>\n"
    "/设管理 <@成员> | /取消管理 <@成员>\n"
    "/全体禁言 | /全体解除禁言"
)


def _target_qq(event: AgentEvent) -> str | None:
    ats = event.message.get("at")
    if not ats:
        return None
    qq = str(ats[0].data.get("qq", "") or "").strip()
    return qq or None


def _minutes(arg: str) -> int:
    for token in arg.split():
        if token.isdigit():
            return max(1, min(int(token), 43200))
    return 10  # 默认 10 分钟


async def _group(registry: AdapterRegistry, event: AgentEvent) -> BaseAdapter | None:
    if not event.group_id:
        await event.reply("该命令仅在群聊中可用。")
        return None
    adapter = registry.get("qq") or (registry.all()[0] if registry.all() else None)
    if adapter is None:
        await event.reply("当前没有可用的 QQ 适配器。")
        return None
    return adapter


def setup(registry) -> None:
    @registry.command("禁言", permission_level=_ADMIN_LEVEL)
    async def ban(event: AgentEvent, registry: AdapterRegistry):
        arg = (event.state.get("command_arg") or "").strip()
        if not arg or not event.group_id:
            await event.reply("用法: /禁言 <@成员> [分钟]")
            return None
        qq = _target_qq(event)
        if not qq:
            await event.reply("请 @ 要禁言的成员,例如: /禁言 @小明 5")
            return None
        minutes = _minutes(arg)
        adapter = await _group(registry, event)
        if adapter is None:
            return None
        try:
            await adapter.set_group_ban(event.group_id, qq, duration=minutes * 60)
            await event.reply(f"🔇 已禁言 {qq} {minutes} 分钟。")
        except Exception:  # noqa: BLE001
            await event.reply(f"禁言失败:{qq} 可能权限不足或该用户不在群内。")
        return None

    @registry.command("解禁", permission_level=_ADMIN_LEVEL)
    async def unban(event: AgentEvent, registry: AdapterRegistry):
        qq = _target_qq(event)
        if not qq or not event.group_id:
            await event.reply("用法: /解禁 <@成员>")
            return None
        adapter = await _group(registry, event)
        if adapter is None:
            return None
        try:
            await adapter.set_group_ban(event.group_id, qq, duration=0)
            await event.reply(f"🔓 已解除 {qq} 的禁言。")
        except Exception:  # noqa: BLE001
            await event.reply(f"解禁失败:{qq}")
        return None

    @registry.command("踢人", permission_level=_ADMIN_LEVEL)
    async def kick(event: AgentEvent, registry: AdapterRegistry):
        qq = _target_qq(event)
        if not qq or not event.group_id:
            await event.reply("用法: /踢人 <@成员>")
            return None
        adapter = await _group(registry, event)
        if adapter is None:
            return None
        try:
            await adapter.set_group_kick(event.group_id, qq)
            await event.reply(f"👢 已将 {qq} 移出群聊。")
        except Exception:  # noqa: BLE001
            await event.reply(f"踢人失败:{qq}")
        return None

    @registry.command("设管理", permission_level=_ADMIN_LEVEL)
    async def set_admin(event: AgentEvent, registry: AdapterRegistry):
        qq = _target_qq(event)
        if not qq or not event.group_id:
            await event.reply("用法: /设管理 <@成员>")
            return None
        adapter = await _group(registry, event)
        if adapter is None:
            return None
        try:
            await adapter.set_group_admin(event.group_id, qq, enable=True)
            await event.reply(f"⭐ 已将 {qq} 设为管理员。")
        except Exception:  # noqa: BLE001
            await event.reply(f"设置失败:{qq}")
        return None

    @registry.command("取消管理", permission_level=_ADMIN_LEVEL)
    async def unset_admin(event: AgentEvent, registry: AdapterRegistry):
        qq = _target_qq(event)
        if not qq or not event.group_id:
            await event.reply("用法: /取消管理 <@成员>")
            return None
        adapter = await _group(registry, event)
        if adapter is None:
            return None
        try:
            await adapter.set_group_admin(event.group_id, qq, enable=False)
            await event.reply(f"🚫 已取消 {qq} 的管理员。")
        except Exception:  # noqa: BLE001
            await event.reply(f"取消管理失败:{qq}")
        return None

    @registry.command("全体禁言", permission_level=_ADMIN_LEVEL)
    async def whole_ban(event: AgentEvent, registry: AdapterRegistry):
        adapter = await _group(registry, event)
        if adapter is None:
            return None
        try:
            await adapter.set_group_whole_ban(event.group_id, enable=True)
            await event.reply("🔇 已开启全体禁言。")
        except Exception:  # noqa: BLE001
            await event.reply("全体禁言失败,可能权限不足。")
        return None

    @registry.command("全体解除禁言", permission_level=_ADMIN_LEVEL)
    async def whole_unban(event: AgentEvent, registry: AdapterRegistry):
        adapter = await _group(registry, event)
        if adapter is None:
            return None
        try:
            await adapter.set_group_whole_ban(event.group_id, enable=False)
            await event.reply("🔓 已解除全体禁言。")
        except Exception:  # noqa: BLE001
            await event.reply("解除全体禁言失败。")
        return None

    @registry.command("群管帮助", permission_level=_ADMIN_LEVEL)
    async def admin_help(event: AgentEvent):
        await event.reply(_HELP)
        return None
