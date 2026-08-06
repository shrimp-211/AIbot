"""系统工具:Shell 执行(权限拦截)、定时任务管理、用户询问。"""
from __future__ import annotations

import asyncio
from typing import Any

from ...security.auth import Decision
from .base import Tool, ToolContext


class BashTool(Tool):
    name = "bash"
    description = "执行 Shell 命令(危险命令会被拦截,curl/wget/sudo 需管理员授权)"
    parameters = {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "要执行的 shell 命令"},
            "timeout": {"type": "integer", "description": "超时秒数,默认30"},
        },
        "required": ["command"],
    }

    async def execute(self, ctx: ToolContext, command: str, timeout: int = 30) -> Any:
        role = ctx.auth.get_role_level(ctx.event.user_id, ctx.event.group_id)
        decision = ctx.auth.check_command(command, role)
        if decision == Decision.DENY:
            return {"error": "危险命令已被系统拦截"}
        if decision == Decision.ASK and role < 7:
            return {"error": "该命令需要管理员授权才能执行"}

        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=min(int(timeout or 30), 120)
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.communicate()
                return {"error": "命令执行超时"}
            out = stdout.decode("utf-8", errors="ignore").strip()
            err = stderr.decode("utf-8", errors="ignore").strip()
            return {
                "exit_code": proc.returncode,
                "stdout": out[:4000],
                "stderr": err[:1000],
            }
        except Exception as exc:  # noqa: BLE001
            return {"error": f"执行失败: {exc}"}


class CronTool(Tool):
    name = "cron"
    description = "创建定时提醒任务(支持自然语言时间,如'明天上午9点'、'每2小时')"
    parameters = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["create", "list", "delete", "parse"],
                "description": "操作类型:create创建/list列出/delete删除/parse解析时间",
            },
            "when": {"type": "string", "description": "时间描述(create时必填)"},
            "text": {"type": "string", "description": "提醒内容(create时必填)"},
            "task_id": {"type": "string", "description": "任务ID(delete时必填)"},
        },
        "required": ["action"],
    }

    async def execute(self, ctx: ToolContext, action: str, **kwargs: Any) -> Any:
        if ctx.cron_manager is None:
            return {"error": "定时任务管理器不可用"}
        action = (action or "").lower()
        if action == "create":
            when, text = kwargs.get("when", ""), kwargs.get("text", "")
            if not when or not text:
                return {"error": "create 需要 when 和 text"}
            return await ctx.cron_manager.add_task(
                ctx.event.session_id, when, text,
                target_group=ctx.event.group_id, target_user=ctx.event.user_id,
            )
        if action == "list":
            return await ctx.cron_manager.list_tasks(ctx.event.session_id)
        if action == "delete":
            return await ctx.cron_manager.delete_task(ctx.event.session_id, kwargs.get("task_id", ""))
        if action == "parse":
            return {"result": await ctx.cron_manager.parse_time(kwargs.get("when", ""))}
        return {"error": f"未知操作: {action}"}


class AskUserTool(Tool):
    name = "ask_user"
    description = "向用户提问以获取完成任务所需的信息(提问后请等待用户回复)"
    parameters = {
        "type": "object",
        "properties": {
            "question": {"type": "string", "description": "要问用户的问题"},
        },
        "required": ["question"],
    }

    async def execute(self, ctx: ToolContext, question: str) -> Any:
        await ctx.event.reply(f"🔎 我需要确认一下:{question}")
        ctx.event.state["awaiting_question"] = question
        return {
            "status": "awaiting",
            "message": f"已向用户提问: {question}。等待用户回复后再继续任务。",
        }
