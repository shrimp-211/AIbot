"""QQ 信息查询工具:群/好友/用户信息。"""
from __future__ import annotations

from typing import Any

from .base import Tool, ToolContext


class QqGroupInfoTool(Tool):
    name = "qq_group_info"
    description = "获取群信息(群名、人数、成员数等)"
    parameters = {
        "type": "object",
        "properties": {
            "group_id": {"type": "string", "description": "群号(默认当前群)"},
        },
    }

    async def execute(self, ctx: ToolContext, group_id: str | None = None) -> Any:
        try:
            data = await ctx.adapter.get_group_info(group_id or ctx.event.group_id)
            return data
        except Exception as exc:  # noqa: BLE001
            return {"error": f"获取群信息失败: {exc}"}


class QqGroupListTool(Tool):
    name = "qq_group_list"
    description = "获取机器人加入的所有群列表"
    parameters = {"type": "object", "properties": {}}

    async def execute(self, ctx: ToolContext) -> Any:
        try:
            groups = await ctx.adapter.get_group_list()
            return [
                {"group_id": g.get("group_id"), "group_name": g.get("group_name")}
                for g in (groups or [])
            ]
        except Exception as exc:  # noqa: BLE001
            return {"error": f"获取群列表失败: {exc}"}


class QqFriendListTool(Tool):
    name = "qq_friend_list"
    description = "获取机器人的好友列表"
    parameters = {"type": "object", "properties": {}}

    async def execute(self, ctx: ToolContext) -> Any:
        try:
            friends = await ctx.adapter.get_friend_list()
            return [
                {"user_id": f.get("user_id"), "nickname": f.get("nickname", "")}
                for f in (friends or [])
            ]
        except Exception as exc:  # noqa: BLE001
            return {"error": f"获取好友列表失败: {exc}"}


class QqStrangerInfoTool(Tool):
    name = "qq_stranger_info"
    description = "查询用户信息(昵称、性别、年龄等)"
    parameters = {
        "type": "object",
        "properties": {
            "user_id": {"type": "string", "description": "用户QQ号"},
        },
        "required": ["user_id"],
    }

    async def execute(self, ctx: ToolContext, user_id: str) -> Any:
        try:
            data = await ctx.adapter.get_stranger_info(user_id)
            return data
        except Exception as exc:  # noqa: BLE001
            return {"error": f"查询用户信息失败: {exc}"}
