"""任务管理工具:创建/查询/列出/更新任务状态(参考 Claude Code Task 工具)。"""
from __future__ import annotations

import time
import uuid
from typing import Any

from .base import Tool, ToolContext


class TaskTools(Tool):
    name = "task"
    description = "管理多步骤任务的状态跟踪(task_create/task_get/task_list/task_update)"
    parameters = {
        "type": "object",
        "properties": {
            "sub_action": {
                "type": "string",
                "enum": ["create", "get", "list", "update"],
                "description": "操作:create创建/get查询/list列出/update更新",
            },
            "task_id": {"type": "string", "description": "任务ID"},
            "title": {"type": "string", "description": "任务标题(create时必填)"},
            "detail": {"type": "string", "description": "任务详情"},
            "status": {
                "type": "string",
                "enum": ["pending", "in_progress", "completed", "failed"],
                "description": "任务状态",
            },
            "group_id": {"type": "string", "description": "所属分组"},
        },
        "required": ["sub_action"],
    }

    def __init__(self, auth=None):
        super().__init__(auth)
        self._tasks: dict[str, dict] = {}

    async def execute(self, ctx: ToolContext, sub_action: str, **kwargs: Any) -> Any:
        action = (sub_action or "").lower()
        if action == "create":
            title = kwargs.get("title", "")
            if not title:
                return {"error": "create 需要 title"}
            tid = kwargs.get("task_id") or uuid.uuid4().hex[:12]
            task = {
                "task_id": tid,
                "title": title,
                "detail": kwargs.get("detail", ""),
                "status": "pending",
                "group_id": kwargs.get("group_id", ""),
                "created_at": int(time.time()),
                "updated_at": int(time.time()),
                "session": ctx.event.session_id,
            }
            self._tasks[tid] = task
            return {"ok": True, "task": task}

        if action == "get":
            tid = kwargs.get("task_id", "")
            task = self._tasks.get(tid)
            if not task:
                return {"error": f"任务不存在: {tid}"}
            return {"task": task}

        if action == "list":
            group = kwargs.get("group_id", "")
            tasks = [t for t in self._tasks.values() if not group or t["group_id"] == group]
            tasks.sort(key=lambda t: t["created_at"])
            return {"count": len(tasks), "tasks": tasks[:50]}

        if action == "update":
            tid = kwargs.get("task_id", "")
            task = self._tasks.get(tid)
            if not task:
                return {"error": f"任务不存在: {tid}"}
            if kwargs.get("status"):
                task["status"] = kwargs["status"]
            if kwargs.get("title"):
                task["title"] = kwargs["title"]
            if kwargs.get("detail"):
                task["detail"] = kwargs["detail"]
            task["updated_at"] = int(time.time())
            return {"ok": True, "task": task}

        return {"error": f"未知操作: {sub_action}"}
