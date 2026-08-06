"""子代理委派工具:提交独立子任务并查询结果。

子代理运行在独立上下文,适合并行调研/拆解复杂任务,结果不污染主对话。
"""
from __future__ import annotations

from typing import Any

from .base import Tool, ToolContext


class SubagentTool(Tool):
    name = "subagent"
    description = (
        "委派独立子代理执行任务(subagent_submit/subagent_get)。"
        "适合把耗时或独立的小任务(如搜索调研)丢给子代理并行处理;"
        "submit 返回 job_id,之后用 get 轮询结果。agent_type: "
        "explore(只读调研)/plan(规划)/general(全工具执行)"
    )
    parameters = {
        "type": "object",
        "properties": {
            "sub_action": {
                "type": "string",
                "enum": ["submit", "get"],
                "description": "submit=提交任务 / get=查询结果",
            },
            "agent_type": {
                "type": "string",
                "enum": ["explore", "plan", "general"],
                "description": "子代理类型,默认 explore",
            },
            "prompt": {"type": "string", "description": "任务描述(submit 时必填)"},
            "job_id": {"type": "string", "description": "任务 ID(get 时必填)"},
        },
        "required": ["sub_action"],
    }

    async def execute(self, ctx: ToolContext, sub_action: str, **kwargs: Any) -> Any:
        mgr = ctx.subagent_manager
        if mgr is None:
            return "子代理功能不可用(未初始化)"

        action = (sub_action or "").lower()
        if action == "submit":
            prompt = (kwargs.get("prompt") or "").strip()
            if not prompt:
                return {"error": "submit 需要 prompt"}
            agent_type = kwargs.get("agent_type", "explore")
            registry = ctx.extra.get("tool_registry")
            schemas = registry.schemas() if registry is not None else None
            job_id = mgr.submit(prompt, agent_type=agent_type, tool_schemas=schemas)
            return {
                "ok": True,
                "job_id": job_id,
                "agent_type": agent_type,
                "tip": "用 subagent_get(job_id=...) 查询结果",
            }

        if action == "get":
            job_id = (kwargs.get("job_id") or "").strip()
            if not job_id:
                return {"error": "get 需要 job_id"}
            result = await mgr.get_result(job_id)
            if result.get("status") == "done":
                return {"status": "done", "result": result["result"]}
            if result.get("status") == "running":
                return {"status": "running", "tip": "任务仍在执行,稍后再查"}
            return result

        return {"error": f"未知操作: {sub_action}"}
