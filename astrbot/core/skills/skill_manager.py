"""技能管理器(compat stub;本项目技能由 src/agent/skills 管理)。"""

from __future__ import annotations


class SkillManager:
    def __init__(self, *args, **kwargs) -> None:
        self.skills = []

    async def get_skills(self, *args, **kwargs):
        return []

    async def get_skill(self, *args, **kwargs):
        return None
