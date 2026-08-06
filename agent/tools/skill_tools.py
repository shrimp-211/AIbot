"""技能(Skills)管理工具:列表 / 激活 / 停用。"""
from __future__ import annotations

from typing import Any

from .base import Tool, ToolContext


class SkillListTool(Tool):
    name = "skill_list"
    description = "列出所有可用技能的名称与用途"
    parameters: dict[str, Any] = {"type": "object", "properties": {}}

    async def execute(self, ctx: ToolContext, **kwargs: Any) -> Any:
        skills = ctx.skills.all() if ctx.skills else []
        if not skills:
            return {"ok": True, "skills": [], "message": "当前没有可用技能"}
        active = ctx.skills.active(ctx.event.session_id) if ctx.skills else None
        return {
            "ok": True,
            "active": active.name if active else None,
            "skills": [
                {
                    "name": s.name,
                    "description": s.description,
                    "tools": s.tools,
                    "plan_only": s.plan_only,
                }
                for s in skills
            ],
        }


class SkillUseTool(Tool):
    name = "skill_use"
    description = "激活指定技能,后续回复将遵循该技能的工具白名单与指令"
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "技能名称,见 skill_list"}
        },
        "required": ["name"],
    }

    async def execute(self, ctx: ToolContext, **kwargs: Any) -> Any:
        name = kwargs.get("name", "")
        if ctx.skills is None:
            return {"error": "技能系统未启用"}
        result = ctx.skills.activate(ctx.event.session_id, name)
        if "error" in result:
            return result
        return {
            "ok": True,
            "message": f"已激活技能「{name}」,请在回复中遵循其指令。输入 skill_stop 可停用。",
            "description": result.get("description", ""),
        }


class SkillStopTool(Tool):
    name = "skill_stop"
    description = "停用当前激活的技能,恢复默认通用模式"
    parameters: dict[str, Any] = {"type": "object", "properties": {}}

    async def execute(self, ctx: ToolContext, **kwargs: Any) -> Any:
        if ctx.skills is None:
            return {"error": "技能系统未启用"}
        return ctx.skills.deactivate(ctx.event.session_id)
