"""技能注册中心 — 管理 Skills 的生命周期.

Skills 是预定义的可复用能力包，通过 SKILL.md 文件定义。
参考 Claude Code 的 Skills 系统设计。

SKILL.md 格式:
```markdown
---
name: my-skill
description: 技能描述
when_to_use: 何时使用
---

## 指令
具体的技能指令内容...
```
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml


class Skill:
    """单个技能的封装."""

    def __init__(self, name: str, description: str, content: str, metadata: dict | None = None):
        self.name = name
        self.description = description
        self.content = content
        self.metadata = metadata or {}

    def __repr__(self) -> str:
        return f"<Skill {self.name}>"


class SkillRegistry:
    """技能注册中心."""

    def __init__(self):
        self._skills: dict[str, Skill] = {}

    def register(self, skill: Skill) -> None:
        self._skills[skill.name] = skill

    def get(self, name: str) -> Skill | None:
        return self._skills.get(name)

    def get_all(self) -> list[Skill]:
        return list(self._skills.values())

    def get_descriptions(self) -> list[dict]:
        return [
            {"name": s.name, "description": s.description}
            for s in self._skills.values()
        ]

    def load_builtin(self) -> None:
        """加载内置技能."""
        builtin_dir = Path(__file__).parent / "builtin"
        if not builtin_dir.exists():
            return

        for md_file in builtin_dir.glob("**/*.md"):
            self._load_from_markdown(md_file)

    def _load_from_markdown(self, path: Path) -> None:
        """从 SKILL.md 文件加载技能定义."""
        text = path.read_text(encoding="utf-8")

        # 解析 YAML frontmatter
        metadata = {}
        content = text
        if text.startswith("---"):
            parts = text.split("---", 2)
            if len(parts) >= 3:
                try:
                    metadata = yaml.safe_load(parts[1]) or {}
                except yaml.YAMLError:
                    pass
                content = parts[2].strip()

        name = metadata.get("name", path.stem)
        description = metadata.get("description", "")

        skill = Skill(name=name, description=description, content=content, metadata=metadata)
        self.register(skill)

    def __len__(self) -> int:
        return len(self._skills)
