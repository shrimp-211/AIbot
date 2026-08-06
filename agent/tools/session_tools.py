"""会话检查点工具:保存/恢复/列出对话上下文(参考 Gemini CLI checkpointing)。

检查点存储当前会话的工作记忆,跨重启可恢复,适合长时间对话中途存档。
"""
from __future__ import annotations

import time
from typing import Any

from .base import Tool, ToolContext


class SessionSaveTool(Tool):
    name = "session_save"
    description = (
        "保存当前会话的上下文快照(检查点),之后可用 session_load 恢复。"
        "适合重要对话中途存档,或用户要求'记住当前进度'时使用。"
    )
    parameters = {"type": "object", "properties": {}}

    async def execute(self, ctx: ToolContext) -> Any:
        if ctx.memory is None:
            return "记忆功能不可用"
        return ctx.memory.save_checkpoint(ctx.event.session_id)


class SessionLoadTool(Tool):
    name = "session_load"
    description = "恢复之前 session_save 保存的会话检查点(回到当时的对话上下文)。"
    parameters = {"type": "object", "properties": {}}

    async def execute(self, ctx: ToolContext) -> Any:
        if ctx.memory is None:
            return "记忆功能不可用"
        result = ctx.memory.load_checkpoint(ctx.event.session_id)
        if result is None:
            return {"error": "该会话没有已保存的检查点,可先用 session_save 保存"}
        return result


class SessionListTool(Tool):
    name = "session_list"
    description = "列出所有已保存的会话检查点(会话ID / 保存时间 / 消息数)。"
    parameters = {"type": "object", "properties": {}}

    async def execute(self, ctx: ToolContext) -> Any:
        if ctx.memory is None:
            return "记忆功能不可用"
        cps = ctx.memory.list_checkpoints()
        if not cps:
            return {"checkpoints": [], "tip": "暂无检查点,可先用 session_save 保存"}
        for cp in cps:
            cp["saved_at_str"] = time.strftime("%Y-%m-%d %H:%M", time.localtime(cp["saved_at"]))
        return {"count": len(cps), "checkpoints": cps}
