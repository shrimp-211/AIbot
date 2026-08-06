"""Plan 工具:复杂任务的分步计划生成与跟踪(参考 Claude Code Plan 模式)。

为复杂/多步任务先规划再执行:plan_create 生成计划存库,plan_view 查看,
plan_update 标记进度,plan_finish 完结。配合 task 工具做细粒度任务跟踪。
"""
from __future__ import annotations

import time
import uuid
from typing import Any

from .base import Tool, ToolContext


class PlanTool(Tool):
    name = "plan"
    description = (
        "为复杂任务生成/查看/更新分步计划(plan_create/plan_view/plan_update/"
        "plan_finish)。处理多步骤任务时应先创建计划,再逐步执行并更新进度。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "sub_action": {
                "type": "string",
                "enum": ["create", "view", "update", "finish"],
                "description": "create生成/view查看/update更新进度/finish完结",
            },
            "goal": {"type": "string", "description": "任务目标(create时必填)"},
            "steps": {
                "type": "array",
                "items": {"type": "string"},
                "description": "步骤列表(create时必填)",
            },
            "plan_id": {"type": "string", "description": "计划ID(update/finish时需要)"},
            "step_index": {"type": "integer", "description": "步骤序号(0开始)"},
            "status": {
                "type": "string",
                "enum": ["pending", "in_progress", "completed"],
                "description": "步骤状态",
            },
        },
        "required": ["sub_action"],
    }

    async def execute(self, ctx: ToolContext, sub_action: str, **kwargs: Any) -> Any:
        action = (sub_action or "").lower()
        db = ctx.db
        plans = db.get("plans", {})

        if action == "create":
            goal = (kwargs.get("goal") or "").strip()
            steps = kwargs.get("steps") or []
            if not goal:
                return {"error": "create 需要 goal"}
            steps = [str(s).strip() for s in steps if str(s).strip()]
            if not steps:
                return {"error": "create 需要 steps 步骤列表"}
            plan_id = uuid.uuid4().hex[:10]
            plan = {
                "plan_id": plan_id,
                "goal": goal,
                "steps": steps,
                "statuses": ["pending"] * len(steps),
                "session": ctx.event.session_id,
                "created_at": int(time.time()),
            }
            plans[plan_id] = plan
            db.set("plans", plans)
            return {"ok": True, "plan_id": plan_id, "goal": goal, "step_count": len(steps)}

        if action == "view":
            plan_id = kwargs.get("plan_id") or ""
            if plan_id:
                plan = plans.get(plan_id)
                if not plan:
                    return {"error": f"计划不存在: {plan_id}"}
                candidates = [plan]
            else:
                candidates = [
                    p for p in plans.values() if p.get("session") == ctx.event.session_id
                ]
            if not candidates:
                return {"error": "该会话暂无计划,可先 plan_create"}
            result = []
            for plan in candidates[-3:]:
                steps_view = [
                    {"index": i, "status": st, "step": s}
                    for i, (s, st) in enumerate(zip(plan["steps"], plan["statuses"]))
                ]
                result.append(
                    {
                        "plan_id": plan["plan_id"],
                        "goal": plan["goal"],
                        "steps": steps_view,
                        "progress": f"{sum(1 for s in plan['statuses'] if s == 'completed')}/{len(plan['steps'])}",
                    }
                )
            return {"plans": result}

        if action == "update":
            plan_id = kwargs.get("plan_id") or ""
            plan = plans.get(plan_id)
            if not plan:
                return {"error": f"计划不存在: {plan_id}"}
            step_index = kwargs.get("step_index")
            status = kwargs.get("status")
            if step_index is None or status not in ("pending", "in_progress", "completed"):
                return {"error": "update 需要 step_index 和 status(pending/in_progress/completed)"}
            try:
                idx = int(step_index)
                if not 0 <= idx < len(plan["steps"]):
                    return {"error": f"步骤序号越界: {idx}"}
                plan["statuses"][idx] = status
            except (TypeError, ValueError):
                return {"error": f"非法的 step_index: {step_index}"}
            plans[plan_id] = plan
            db.set("plans", plans)
            return {"ok": True, "plan_id": plan_id, "step_index": idx, "status": status}

        if action == "finish":
            plan_id = kwargs.get("plan_id") or ""
            plan = plans.pop(plan_id, None)
            if not plan:
                return {"error": f"计划不存在: {plan_id}"}
            db.set("plans", plans)
            return {"ok": True, "finished": plan_id}

        return {"error": f"未知操作: {sub_action}"}
