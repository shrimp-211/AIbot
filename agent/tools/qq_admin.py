"""QQ 群管理工具(需要管理员权限)。"""
from __future__ import annotations

from typing import Any

from .base import Tool, ToolContext


def _group(ctx: ToolContext, group_id: str | int | None) -> str:
    return str(group_id or ctx.event.group_id or "")


class QqKickTool(Tool):
    name = "qq_kick"
    description = "将群成员移出群聊(需管理员权限)"
    parameters = {
        "type": "object",
        "properties": {
            "user_id": {"type": "string", "description": "要踢出的用户QQ号"},
            "group_id": {"type": "string", "description": "群号(默认当前群)"},
            "reject_add_request": {"type": "boolean", "description": "是否拒绝加群请求"},
        },
        "required": ["user_id"],
    }
    permission_level = 7

    async def execute(self, ctx: ToolContext, user_id: str, **kwargs: Any) -> Any:
        try:
            await ctx.adapter.set_group_kick(
                _group(ctx, kwargs.get("group_id")), user_id,
                reject_add_request=bool(kwargs.get("reject_add_request", False)),
            )
            return {"ok": True, "message": f"已将 {user_id} 移出群聊"}
        except Exception as exc:  # noqa: BLE001
            return {"error": f"踢人失败: {exc}"}


class QqMuteTool(Tool):
    name = "qq_mute"
    description = "禁言群成员(需管理员权限,duration 单位秒)"
    parameters = {
        "type": "object",
        "properties": {
            "user_id": {"type": "string", "description": "要禁言的用户QQ号"},
            "group_id": {"type": "string", "description": "群号(默认当前群)"},
            "duration": {"type": "integer", "description": "禁言时长秒数,默认600秒"},
        },
        "required": ["user_id"],
    }
    permission_level = 7

    async def execute(self, ctx: ToolContext, user_id: str, duration: int = 600, **kwargs: Any) -> Any:
        try:
            await ctx.adapter.set_group_ban(
                _group(ctx, kwargs.get("group_id")), user_id, duration=int(duration or 600)
            )
            return {"ok": True, "message": f"已将 {user_id} 禁言 {duration or 600} 秒"}
        except Exception as exc:  # noqa: BLE001
            return {"error": f"禁言失败: {exc}"}


class QqSetAdminTool(Tool):
    name = "qq_set_admin"
    description = "设置/取消群管理员(需群主权限)"
    parameters = {
        "type": "object",
        "properties": {
            "user_id": {"type": "string", "description": "用户QQ号"},
            "group_id": {"type": "string", "description": "群号(默认当前群)"},
            "enable": {"type": "boolean", "description": "设为管理员(默认true)"},
        },
        "required": ["user_id"],
    }
    permission_level = 7

    async def execute(self, ctx: ToolContext, user_id: str, enable: bool = True, **kwargs: Any) -> Any:
        try:
            await ctx.adapter.set_group_admin(
                _group(ctx, kwargs.get("group_id")), user_id, enable=bool(enable)
            )
            return {"ok": True, "message": f"已{'设置' if enable else '取消'}{user_id} 为管理员"}
        except Exception as exc:  # noqa: BLE001
            return {"error": f"操作失败: {exc}"}


class QqEssenceTool(Tool):
    name = "qq_essence"
    description = "将消息设为群精华消息(需管理员权限)"
    parameters = {
        "type": "object",
        "properties": {
            "message_id": {"type": "integer", "description": "消息ID"},
        },
        "required": ["message_id"],
    }
    permission_level = 7

    async def execute(self, ctx: ToolContext, message_id: int) -> Any:
        try:
            await ctx.adapter.set_essence_msg(int(message_id))
            return {"ok": True, "message": "已将消息设为精华"}
        except Exception as exc:  # noqa: BLE001
            return {"error": f"设置精华失败: {exc}"}


class QqGroupAnnounceTool(Tool):
    name = "qq_group_announce"
    description = "发布群公告(需管理员权限)"
    parameters = {
        "type": "object",
        "properties": {
            "content": {"type": "string", "description": "公告内容"},
            "group_id": {"type": "string", "description": "群号(默认当前群)"},
        },
        "required": ["content"],
    }
    permission_level = 7

    async def execute(self, ctx: ToolContext, content: str, **kwargs: Any) -> Any:
        try:
            await ctx.adapter.send_group_notice(_group(ctx, kwargs.get("group_id")), content)
            return {"ok": True, "message": "公告已发布"}
        except Exception as exc:  # noqa: BLE001
            return {"error": f"发布公告失败: {exc}"}


class QqGroupFileListTool(Tool):
    name = "qq_group_file_list"
    description = "获取群文件列表(根目录)"
    parameters = {
        "type": "object",
        "properties": {
            "group_id": {"type": "string", "description": "群号(默认当前群)"},
        },
    }

    async def execute(self, ctx: ToolContext, **kwargs: Any) -> Any:
        try:
            data = await ctx.adapter.get_group_root_files(_group(ctx, kwargs.get("group_id")))
            return data
        except Exception as exc:  # noqa: BLE001
            return {"error": f"获取群文件失败: {exc}"}
