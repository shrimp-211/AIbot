"""多人格系统:独立 system_prompt + 工具白名单 + 会话级切换(热切换)。"""
from __future__ import annotations

import uuid
from typing import Any


class PersonaManager:
    def __init__(self, db: Any, default_prompt: str = "你是 QQ 群里的智能 AI 助手,乐于助人。"):
        self._db = db
        self._default_prompt = default_prompt
        self._session_personas: dict[str, str] = {}  # session_id -> persona_id

    # ---------- CRUD ----------

    def list(self) -> list[dict]:
        personas = self._db.get("personas", {})
        return [
            {
                "id": pid,
                "name": p.get("name", pid),
                "description": p.get("description", ""),
                "tool_allowlist": p.get("tool_allowlist"),
            }
            for pid, p in personas.items()
        ]

    def get(self, persona_id: str) -> dict | None:
        return self._db.get("personas", {}).get(persona_id)

    def create(
        self,
        persona_id: str | None = None,
        name: str = "",
        system_prompt: str = "",
        description: str = "",
        tool_allowlist: list[str] | None = None,
        begin_dialogs: list[dict] | None = None,
    ) -> dict:
        pid = persona_id or uuid.uuid4().hex[:10]
        personas = self._db.get("personas", {})
        personas[pid] = {
            "name": name or pid,
            "system_prompt": system_prompt,
            "description": description,
            "tool_allowlist": tool_allowlist,  # None=全部工具, []=无工具, [...]=白名单
            "begin_dialogs": begin_dialogs or [],
        }
        self._db.set("personas", personas)
        return {"id": pid, "name": name or pid}

    def update(self, persona_id: str, **fields: Any) -> dict:
        personas = self._db.get("personas", {})
        if persona_id not in personas:
            return {"error": f"人格不存在: {persona_id}"}
        personas[persona_id].update(fields)
        self._db.set("personas", personas)
        return {"ok": True, "id": persona_id}

    def delete(self, persona_id: str) -> dict:
        personas = self._db.get("personas", {})
        if persona_id in personas:
            del personas[persona_id]
            self._db.set("personas", personas)
        # 清理会话引用
        self._session_personas = {
            k: v for k, v in self._session_personas.items() if v != persona_id
        }
        return {"ok": True}

    # ---------- 会话切换 ----------

    def switch(self, session_id: str, persona_id: str | None) -> dict:
        if persona_id is None:
            self._session_personas.pop(session_id, None)
            return {"ok": True, "message": "已恢复默认人格"}
        if self.get(persona_id) is None:
            return {"error": f"人格不存在: {persona_id}"}
        self._session_personas[session_id] = persona_id
        p = self.get(persona_id)
        return {"ok": True, "message": f"已切换到人格: {p.get('name', persona_id)}"}

    def get_prompt(self, session_id: str) -> str:
        pid = self._session_personas.get(session_id)
        if pid:
            p = self.get(pid)
            if p and p.get("system_prompt"):
                return p["system_prompt"]
        return self._default_prompt

    def get_tool_allowlist(self, session_id: str) -> list[str] | None:
        pid = self._session_personas.get(session_id)
        if pid:
            p = self.get(pid)
            if p:
                return p.get("tool_allowlist")
        return None
