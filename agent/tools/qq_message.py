"""QQ 消息操作工具:发送图片/语音、撤回消息、点赞。"""
from __future__ import annotations

from typing import Any

from .base import Tool, ToolContext


class QqSendImageTool(Tool):
    name = "qq_send_image"
    description = "发送图片到当前会话(支持URL或本地路径)"
    parameters = {
        "type": "object",
        "properties": {
            "image_url": {"type": "string", "description": "图片URL或本地文件路径"},
        },
        "required": ["image_url"],
    }

    async def execute(self, ctx: ToolContext, image_url: str) -> Any:
        cq = f"[CQ:image,file={image_url}]"
        if ctx.event.message_type == "group" and ctx.event.group_id:
            await ctx.adapter.send_group_msg(ctx.event.group_id, cq)
        else:
            await ctx.adapter.send_private_msg(ctx.event.user_id, cq)
        return {"ok": True, "message": "图片已发送"}


class QqSendVoiceTool(Tool):
    name = "qq_send_voice"
    description = "发送语音到当前会话"
    parameters = {
        "type": "object",
        "properties": {
            "file": {"type": "string", "description": "语音文件URL或本地路径"},
        },
        "required": ["file"],
    }

    async def execute(self, ctx: ToolContext, file: str) -> Any:
        cq = f"[CQ:record,file={file}]"
        if ctx.event.message_type == "group" and ctx.event.group_id:
            await ctx.adapter.send_group_msg(ctx.event.group_id, cq)
        else:
            await ctx.adapter.send_private_msg(ctx.event.user_id, cq)
        return {"ok": True, "message": "语音已发送"}


class QqRecallTool(Tool):
    name = "qq_recall"
    description = "撤回一条消息(需管理员或消息发送者权限)"
    parameters = {
        "type": "object",
        "properties": {
            "message_id": {"type": "integer", "description": "要撤回的消息ID"},
        },
        "required": ["message_id"],
    }
    permission_level = 1

    async def execute(self, ctx: ToolContext, message_id: int) -> Any:
        try:
            await ctx.adapter.delete_msg(int(message_id))
            return {"ok": True, "message": "消息已撤回"}
        except Exception as exc:  # noqa: BLE001
            return {"error": f"撤回失败: {exc}"}


class QqSendLikeTool(Tool):
    name = "qq_send_like"
    description = "点赞(戳一戳)一个用户"
    parameters = {
        "type": "object",
        "properties": {
            "user_id": {"type": "string", "description": "目标用户QQ号"},
            "times": {"type": "integer", "description": "点赞次数,默认1"},
        },
        "required": ["user_id"],
    }

    async def execute(self, ctx: ToolContext, user_id: str, times: int = 1) -> Any:
        try:
            await ctx.adapter.send_like(user_id, times=int(times or 1))
            return {"ok": True, "message": f"已给 {user_id} 点赞"}
        except Exception as exc:  # noqa: BLE001
            return {"error": f"点赞失败: {exc}"}
