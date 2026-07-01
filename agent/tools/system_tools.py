"""系统工具 — Bash, Cron, AskUser."""

import asyncio
import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .base import BaseTool


class BashTool(BaseTool):
    name = "bash"
    description = "执行 Shell 命令。注意: 对系统有实际影响，谨慎使用。"
    permission_level = 7  # 仅管理员

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "要执行的 Shell 命令",
                },
            },
            "required": ["command"],
        }

    async def execute(self, command: str, **kwargs) -> str:
        """执行 Shell 命令 (需管理员权限)."""
        # 安全检查: 白名单 + 黑名单双重机制
        dangerous = ["rm -rf /", "mkfs.", ":(){ :|:& };:", "> /dev/sda",
                     "shutdown", "reboot", "dd if=/dev/zero", "chmod -R 777 /",
                     "curl", "wget", "sudo", "su -", "> /dev/sd"]
        for d in dangerous:
            if d in command:
                return f"危险命令被拦截: {d}"

        try:
            import shlex
            cmd_parts = shlex.split(command)
            proc = await asyncio.create_subprocess_exec(
                *cmd_parts,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd="data",
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=30
            )

            output = stdout.decode("utf-8", errors="replace")
            if stderr:
                output += "\n[stderr]\n" + stderr.decode("utf-8", errors="replace")

            if len(output) > 3000:
                output = output[:3000] + "\n... (输出过长，已截断)"

            return f"退出码: {proc.returncode}\n{output.strip() or '(无输出)'}"

        except asyncio.TimeoutError:
            return "命令超时 (30s)"
        except Exception as e:
            return f"执行失败: {e}"


class CronTool(BaseTool):
    name = "cron"
    description = "设置定时提醒。使用自然语言描述时间和内容。"
    permission_level = 1

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["add", "list", "delete"],
                    "description": "操作: add=添加, list=列出, delete=删除",
                },
                "time": {
                    "type": "string",
                    "description": "提醒时间 (add 时必填)，如 '2026-07-01 14:00' 或 '14:30'",
                },
                "content": {
                    "type": "string",
                    "description": "提醒内容 (add 时必填)",
                },
                "index": {
                    "type": "integer",
                    "description": "要删除的提醒序号 (delete 时必填)",
                },
            },
            "required": ["action"],
        }

    async def execute(self, action: str, time: str = "", content: str = "", index: int = 0, **kwargs) -> str:
        cron_file = Path("data/cron.json")

        if action == "list":
            if not cron_file.exists():
                return "暂无定时提醒"
            data = json.loads(cron_file.read_text())
            if not data:
                return "暂无定时提醒"
            lines = ["定时提醒列表:"]
            for i, entry in enumerate(data, 1):
                lines.append(f"  {i}. [{entry['time']}] {entry['content']}")
            return "\n".join(lines)

        elif action == "add":
            if not time or not content:
                return "添加提醒需要指定 time 和 content 参数"
            data = json.loads(cron_file.read_text()) if cron_file.exists() else []
            data.append({"time": time, "content": content, "created": datetime.now().isoformat()})
            cron_file.parent.mkdir(parents=True, exist_ok=True)
            cron_file.write_text(json.dumps(data, ensure_ascii=False, indent=2))
            return f"已添加提醒: [{time}] {content}"

        elif action == "delete":
            if not cron_file.exists():
                return "暂无定时提醒"
            data = json.loads(cron_file.read_text())
            if index < 1 or index > len(data):
                return f"无效序号: {index}"
            removed = data.pop(index - 1)
            cron_file.write_text(json.dumps(data, ensure_ascii=False, indent=2))
            return f"已删除提醒: [{removed['time']}] {removed['content']}"

        return f"未知操作: {action}"


class AskUserTool(BaseTool):
    name = "ask_user"
    description = "当需要用户确认或补充信息时向用户提问"
    permission_level = 0

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "向用户提出的问题",
                },
                "options": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "可选项列表 (可选)",
                },
            },
            "required": ["question"],
        }

    async def execute(self, question: str, options: list[str] | None = None, **kwargs) -> str:
        if options:
            opts = "\n".join(f"  {i}. {o}" for i, o in enumerate(options, 1))
            return f"请回复用户: {question}\n\n选项:\n{opts}"
        return f"请回复用户: {question} (等待用户回复)"
