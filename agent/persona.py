"""人格系统 — 参考 AstrBot PersonaManager 设计。

支持多人格配置:
- 每个人格独立 system_prompt
- 工具白名单/黑名单
- 技能白名单
- 预设开场对话 (begin_dialogs)
- 热切换 (/persona 命令)

优先级: 会话强制 > Provider默认 > 系统全局默认
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger


@dataclass
class Persona:
    """人格定义."""
    id: str
    name: str
    description: str = ""
    system_prompt: str = ""
    begin_dialogs: list[dict] = field(default_factory=list)  # [{"role":"user","content":"..."}, ...]
    tools_whitelist: list[str] | None = None  # None = 所有工具
    tools_blacklist: list[str] | None = None
    skills_whitelist: list[str] | None = None
    temperature: float | None = None
    model: str | None = None
    created: float = field(default_factory=time.time)
    updated: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "id": self.id, "name": self.name, "description": self.description,
            "system_prompt": self.system_prompt, "begin_dialogs": self.begin_dialogs,
            "tools_whitelist": self.tools_whitelist, "tools_blacklist": self.tools_blacklist,
            "skills_whitelist": self.skills_whitelist,
            "temperature": self.temperature, "model": self.model,
        }


class PersonaManager:
    """人格管理器 — 参考 AstrBot PersonaManager."""

    def __init__(self, storage_dir: str = "data"):
        self._path = Path(storage_dir) / "personas.json"
        self._personas: dict[str, Persona] = {}
        self._default_id: str = "default"
        self._active_id: str = "default"
        self._session_overrides: dict[str, str] = {}  # session_id → persona_id
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            self._create_defaults()
            return
        try:
            data = json.loads(self._path.read_text())
            for p in data.get("personas", []):
                persona = Persona(**p)
                self._personas[persona.id] = persona
            self._default_id = data.get("default_id", "default")
            self._active_id = self._default_id
        except (json.JSONDecodeError, OSError):
            self._create_defaults()

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps({
            "personas": [p.to_dict() for p in self._personas.values()],
            "default_id": self._default_id,
        }, ensure_ascii=False, indent=2))

    def _create_defaults(self) -> None:
        self._personas["default"] = Persona(
            id="default", name="默认助手", description="通用 QQ 群聊 AI 助手",
            system_prompt="你是友好的QQ群聊AI助手。回复简洁、自然、有帮助。",
        )
        # 角色扮演人格
        self._personas["cat"] = Persona(
            id="cat", name="猫娘", description="傲娇猫娘角色扮演",
            system_prompt="你是傲娇猫娘。每句话结尾加'喵~'。自称'人家'。"
                         "对主人态度傲娇但实际很关心。拒绝讨论政治敏感话题。",
            tools_blacklist=["bash", "qq_kick", "qq_mute", "qq_set_admin"],
        )
        self._personas["coder"] = Persona(
            id="coder", name="代码专家", description="技术编程助手",
            system_prompt="你是资深软件工程师。代码示例清晰、解释深入。"
                         "优先使用代码解决问题。用中文回复，代码保留英文。",
            tools_whitelist=["file_read", "file_write", "glob", "grep", "bash",
                            "web_search", "web_fetch", "agent"],
        )
        self._save()
        logger.info(f"已初始化 {len(self._personas)} 个人格")

    # ---- CRUD ----

    def list_all(self) -> list[dict]:
        return [{"id": p.id, "name": p.name, "description": p.description,
                 "active": p.id == self._active_id}
                for p in self._personas.values()]

    def get(self, persona_id: str | None = None) -> Persona:
        pid = persona_id or self._active_id
        return self._personas.get(pid, self._personas["default"])

    def get_active(self, session_id: str = "") -> Persona:
        """获取当前生效的人格 (考虑会话覆盖)."""
        if session_id and session_id in self._session_overrides:
            pid = self._session_overrides[session_id]
            return self._personas.get(pid, self._personas["default"])
        return self._personas.get(self._active_id, self._personas["default"])

    def set_active(self, persona_id: str) -> bool:
        if persona_id in self._personas:
            self._active_id = persona_id
            return True
        return False

    def override_session(self, session_id: str, persona_id: str) -> None:
        if persona_id in self._personas:
            self._session_overrides[session_id] = persona_id
        else:
            self._session_overrides.pop(session_id, None)

    def clear_session_override(self, session_id: str) -> None:
        self._session_overrides.pop(session_id, None)

    def add(self, persona: Persona) -> None:
        self._personas[persona.id] = persona
        persona.updated = time.time()
        self._save()

    def remove(self, persona_id: str) -> bool:
        if persona_id == "default":
            return False
        if persona_id in self._personas:
            del self._personas[persona_id]
            if self._active_id == persona_id:
                self._active_id = "default"
            self._save()
            return True
        return False

    def get_system_prompt(self, session_id: str = "") -> str:
        return self.get_active(session_id).system_prompt

    def get_tool_blacklist(self, session_id: str = "") -> list[str] | None:
        return self.get_active(session_id).tools_blacklist

    def get_tool_whitelist(self, session_id: str = "") -> list[str] | None:
        return self.get_active(session_id).tools_whitelist
