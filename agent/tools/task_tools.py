"""任务管理工具:创建/查询/列出/更新任务状态(参考 Claude Code Task 工具)。

任务持久化到 JsonKV(tasks 键),跨重启保留;按会话隔离。
"""
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

    @staticmethod
    def _load(ctx: ToolContext) -> dict[str, dict]:
        return dict(ctx.db.get("tasks", {}) or {})

    @staticmethod
    def _save(ctx: ToolContext, tasks: dict[str, dict]) -> None:
        # 清理:每个会话最多保留 100 条任务
        if len(tasks) > 1000:
            tasks = dict(sorted(tasks.items(), key=lambda kv: kv[1].get("created_at", 0))[-1000:])
        ctx.db.set("tasks", tasks)

    async def execute(self, ctx: ToolContext, sub_action: str, **kwargs: Any) -> Any:
        action = (sub_action or "").lower()
        session_id = ctx.event.session_id
        tasks = self._load(ctx)

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
                "session": session_id,
            }
            tasks[tid] = task
            self._save(ctx, tasks)
            return {"ok": True, "task": task}

        if action == "get":
            tid = kwargs.get("task_id", "")
            task = tasks.get(tid)
            if not task:
                return {"error": f"任务不存在: {tid}"}
            return {"task": task}

        if action == "list":
            group = kwargs.get("group_id", "")
            mine = [
                t
                for t in tasks.values()
                if t.get("session") == session_id
                and (not group or t.get("group_id") == group)
            ]
            mine.sort(key=lambda t: t.get("created_at", 0))
            return {"count": len(mine), "tasks": mine[:50]}

        if action == "update":
            tid = kwargs.get("task_id", "")
            task = tasks.get(tid)
            if not task:
                return {"error": f"任务不存在: {tid}"}
            if kwargs.get("status"):
                task["status"] = kwargs["status"]
            if kwargs.get("title"):
                task["title"] = kwargs["title"]
            if kwargs.get("detail"):
                task["detail"] = kwargs["detail"]
            task["updated_at"] = int(time.time())
            self._save(ctx, tasks)
            return {"ok": True, "task": task}

        return {"error": f"未知操作: {sub_action}"}
