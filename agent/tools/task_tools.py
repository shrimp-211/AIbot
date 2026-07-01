"""任务管理工具 — TodoWrite + TaskCreate/Get/List/Update.

参考 Claude Code 的任务系统设计:
- TodoWriteTool: 写入待办事项列表，帮助 Agent 规划复杂任务
- TaskCreateTool: 创建后台任务
- TaskGetTool: 获取任务详情
- TaskListTool: 列出所有任务
- TaskUpdateTool: 更新任务状态
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from .base import BaseTool


class TodoWriteTool(BaseTool):
    name = "todo_write"
    description = "创建和管理待办事项列表，用于跟踪复杂多步骤任务的进度。每个 todo 包含 content 和 status (pending/in_progress/completed)。"
    permission_level = 0

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "todos": {
                    "type": "array",
                    "description": "待办事项列表",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string", "description": "唯一标识符"},
                            "content": {"type": "string", "description": "任务内容"},
                            "status": {
                                "type": "string",
                                "enum": ["pending", "in_progress", "completed"],
                                "description": "任务状态",
                            },
                        },
                        "required": ["id", "content", "status"],
                    },
                },
            },
            "required": ["todos"],
        }

    async def execute(self, todos: list[dict], **kwargs) -> str:
        try:
            path = Path("data/todos.json")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(todos, ensure_ascii=False, indent=2))

            counts = {
                "pending": sum(1 for t in todos if t["status"] == "pending"),
                "in_progress": sum(1 for t in todos if t["status"] == "in_progress"),
                "completed": sum(1 for t in todos if t["status"] == "completed"),
            }
            return f"待办事项已更新 (共 {len(todos)} 项):\n" \
                   f"  ⬜ 待处理: {counts['pending']}\n" \
                   f"  🔄 进行中: {counts['in_progress']}\n" \
                   f"  ✅ 已完成: {counts['completed']}"
        except Exception as e:
            return f"更新失败: {e}"


class TaskCreateTool(BaseTool):
    name = "task_create"
    description = "创建一个后台任务。用于需要异步执行、长时间运行或独立上下文的任务。"
    permission_level = 1

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "subject": {
                    "type": "string",
                    "description": "任务标题",
                },
                "description": {
                    "type": "string",
                    "description": "任务详细描述",
                },
                "prompt": {
                    "type": "string",
                    "description": "任务要执行的 prompt/指令",
                },
            },
            "required": ["subject", "prompt"],
        }

    async def execute(self, subject: str, prompt: str, description: str = "", **kwargs) -> str:
        import uuid

        tasks = TaskCreateTool._load_tasks()
        task_id = str(uuid.uuid4())[:8]
        task = {
            "id": task_id,
            "subject": subject,
            "description": description,
            "prompt": prompt,
            "status": "pending",
            "result": "",
            "created": time.time(),
        }
        tasks.append(task)
        TaskCreateTool._save_tasks(tasks)
        return f"任务已创建:\n  ID: {task_id}\n  标题: {subject}\n  状态: pending\n  (任务将在后台执行)"

    @staticmethod
    def _load_tasks() -> list:
        path = Path("data/tasks.json")
        if path.exists():
            try:
                return json.loads(path.read_text())
            except (json.JSONDecodeError, OSError):
                pass
        return []

    @staticmethod
    def _save_tasks(tasks: list) -> None:
        path = Path("data/tasks.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(tasks, ensure_ascii=False, indent=2))


class TaskGetTool(BaseTool):
    name = "task_get"
    description = "获取指定后台任务的信息和结果"
    permission_level = 0

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "taskId": {
                    "type": "string",
                    "description": "任务 ID",
                },
            },
            "required": ["taskId"],
        }

    async def execute(self, taskId: str, **kwargs) -> str:
        tasks = TaskCreateTool._load_tasks()
        for t in tasks:
            if t["id"] == taskId:
                return (
                    f"任务 [{t['id']}]: {t['subject']}\n"
                    f"  状态: {t['status']}\n"
                    f"  描述: {t['description']}\n"
                    f"  结果: {t.get('result', '(无)')[:1000]}"
                )
        return f"未找到任务: {taskId}"


class TaskListTool(BaseTool):
    name = "task_list"
    description = "列出所有后台任务及其状态"
    permission_level = 0

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {},
        }

    async def execute(self, **kwargs) -> str:
        tasks = TaskCreateTool._load_tasks()
        if not tasks:
            return "暂无后台任务"

        lines = [f"后台任务列表 ({len(tasks)} 个):"]
        status_icons = {"pending": "⬜", "in_progress": "🔄", "completed": "✅", "failed": "❌"}
        for t in tasks:
            icon = status_icons.get(t["status"], "❓")
            lines.append(f"  {icon} [{t['id']}] {t['subject']} — {t['status']}")
        return "\n".join(lines)


class TaskUpdateTool(BaseTool):
    name = "task_update"
    description = "更新后台任务的状态或结果"
    permission_level = 1

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "taskId": {
                    "type": "string",
                    "description": "任务 ID",
                },
                "status": {
                    "type": "string",
                    "enum": ["pending", "in_progress", "completed", "failed", "deleted"],
                    "description": "新状态",
                },
                "result": {
                    "type": "string",
                    "description": "任务结果 (仅 completed 时填写)",
                },
            },
            "required": ["taskId", "status"],
        }

    async def execute(self, taskId: str, status: str, result: str = "", **kwargs) -> str:
        tasks = TaskCreateTool._load_tasks()
        for t in tasks:
            if t["id"] == taskId:
                t["status"] = status
                if result:
                    t["result"] = result
                if status == "deleted":
                    tasks.remove(t)
                TaskCreateTool._save_tasks(tasks)
                icon = {"pending": "⬜", "in_progress": "🔄", "completed": "✅", "failed": "❌", "deleted": "🗑️"}.get(status, "❓")
                return f"{icon} 任务 [{taskId}] 状态已更新为: {status}"
        return f"未找到任务: {taskId}"
