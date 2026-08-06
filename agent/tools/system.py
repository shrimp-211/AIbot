"""系统工具:Shell 执行(权限拦截 + 沙箱 + 审计)、定时任务管理、用户询问。"""
from __future__ import annotations

import asyncio
import os
import shlex
from typing import Any

from loguru import logger

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
            "workdir": {"type": "string", "description": "执行目录,默认当前目录"},
        },
        "required": ["command"],
    }

    @staticmethod
    def _sandbox_wrap(command: str, workdir: str) -> str:
        """用 Docker 沙箱执行命令(参考 Codex/Gemini CLI 沙箱)。"""
        vol = shlex.quote(f"{os.path.abspath(workdir or '.')}:/workspace")
        inner = shlex.quote(command)
        return f"docker run --rm -v {vol} -w /workspace alpine sh -c {inner}"

    async def execute(self, ctx: ToolContext, command: str, timeout: int = 30, workdir: str = ".") -> Any:
        role = ctx.auth.get_role_level(ctx.event.user_id, ctx.event.group_id)
        decision = ctx.auth.check_command(command, role)
        if decision == Decision.DENY:
            await self._audit(ctx, command, "deny", "危险命令被拦截")
            return {"error": "危险命令已被系统拦截"}
        if decision == Decision.ASK and role < 7:
            await self._audit(ctx, command, "ask_deny", "需要管理员授权")
            return {"error": "该命令需要管理员授权才能执行"}

        # 可信目录检查(空白名单 = 全部可信)
        if not ctx.auth.is_path_trusted(workdir or "."):
            await self._audit(ctx, command, "deny", "工作目录不在可信范围内")
            return {"error": f"工作目录不在可信范围内: {workdir}"}

        # 沙箱模式
        if ctx.auth.sandbox_enabled:
            command = self._sandbox_wrap(command, workdir or ".")

        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                cwd=workdir or ".",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=min(int(timeout or 30), 120)
                )
            except asyncio.TimeoutError:
                proc.kill()
                try:
                    await proc.communicate()
                except Exception:  # noqa: BLE001
                    pass
                await self._audit(ctx, command, "allow", "", "timeout")
                return {"error": "命令执行超时"}
            except asyncio.CancelledError:
                # 任务被取消时清理子进程,防止泄漏
                proc.kill()
                raise
            out = stdout.decode("utf-8", errors="ignore").strip()
            err = stderr.decode("utf-8", errors="ignore").strip()
            await self._audit(ctx, command, "allow", "", "ok" if proc.returncode == 0 else "error")
            return {
                "exit_code": proc.returncode,
                "stdout": out[:4000],
                "stderr": err[:1000],
            }
        except Exception as exc:  # noqa: BLE001
            await self._audit(ctx, command, "allow", str(exc), "error")
            # 不向模型泄漏异常细节(CLAUDE.md 规范 3),详情走日志
            logger.warning("bash 命令执行失败: {}", exc)
            return {"error": "命令执行失败"}

    async def _audit(self, ctx: ToolContext, command: str, decision: str, reason: str, status: str = "ok") -> None:
        audit = ctx.extra.get("audit_logger")
        if audit is None:
            return
        try:
            await audit.log(
                user_id=ctx.event.user_id,
                group_id=ctx.event.group_id,
                action="bash",
                tool_name="bash",
                detail=command[:500],
                decision=decision,
                reason=reason,
                status=status,
            )
        except Exception:  # noqa: BLE001
            pass


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
        from ...adapter.message import escape_cq

        await ctx.event.reply(f"🔎 我需要确认一下:{escape_cq(question)}")
        if ctx.memory is not None:
            ctx.memory.set_pending_question(ctx.event.session_id, question)
        return {
            "status": "awaiting",
            "message": f"已向用户提问: {question}。等待用户回复后再继续任务。",
        }
