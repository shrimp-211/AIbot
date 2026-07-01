"""人格切换工具 — LLM可调用切换人格."""

from typing import Any
from .base import BaseTool


class PersonaSwitchTool(BaseTool):
    name = "persona_switch"
    description = "切换当前对话的人格/角色。可用人格包括: default(默认助手), cat(猫娘), coder(代码专家) 以及用户自定义的人格。"
    permission_level = 0

    manager = None  # PersonaManager

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {
            "persona_id": {"type": "string", "description": "人格ID"},
        }, "required": ["persona_id"]}

    async def execute(self, persona_id: str, **kwargs) -> str:
        if not self.manager:
            return "人格系统未初始化"
        if self.manager.set_active(persona_id):
            p = self.manager.get(persona_id)
            return f"人格已切换为: {p.name}\n{p.description}\n\n---\n{p.system_prompt[:300]}"
        return f"人格不存在: {persona_id}。可用: {', '.join(p['id'] for p in self.manager.list_all())}"


class PersonaListTool(BaseTool):
    name = "persona_list"
    description = "列出所有可用的人格/角色"
    permission_level = 0
    manager = None

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def execute(self, **kwargs) -> str:
        if not self.manager:
            return "人格系统未初始化"
        personas = self.manager.list_all()
        lines = ["可用人格:"]
        for p in personas:
            active = " [当前]" if p["active"] else ""
            lines.append(f"  • {p['id']}: {p['name']} — {p['description'][:50]}{active}")
        return "\n".join(lines)
