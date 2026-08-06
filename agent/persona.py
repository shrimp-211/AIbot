"""多人格系统:独立 system_prompt + 工具白名单 + 会话级切换(热切换)。

人格来源二合一(参照 AstrBot prompts 目录 + 内置人格):
- 文件式:data/personas/*.md(文件名为人格 id,内容为 system_prompt;首行 `# 名字` 可选)
- 数据库式:经 /persona create 或 WebUI 创建
文件人格优先级高于数据库人格(同名覆盖)。
"""
from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import Any


class PersonaManager:
    def __init__(
        self,
        db: Any,
        default_prompt: str = "你是 QQ 群里的智能 AI 助手,乐于助人。",
        personas_dir: str | Path | None = None,
    ):
        self._db = db
        self._default_prompt = default_prompt
        self._personas_dir = Path(personas_dir) if personas_dir else None
        # 文件式人格(filename stem -> persona dict)
        self._file_personas: dict[str, dict] = {}
        # session_id -> {"persona": persona_id, "ts": 切换时间}
        self._session_personas: dict[str, dict] = {}
        self._access_count = 0
        if self._personas_dir is not None:
            self.reload_files()

    # ---------- 文件式人格 ----------

    def reload_files(self) -> int:
        """从 personas_dir 扫描 *.md 加载文件式人格,返回数量。"""
        if self._personas_dir is None:
            return 0
        self._personas_dir.mkdir(parents=True, exist_ok=True)
        self._file_personas.clear()
        for path in sorted(self._personas_dir.glob("*.md")):
            try:
                content = path.read_text(encoding="utf-8").strip()
            except OSError:
                continue
            stem = path.stem
            name, description = stem, ""
            if content.startswith("# "):
                first, _, rest = content.partition("\n")
                name = first.lstrip("# ").strip() or stem
                content = rest.strip()
            self._file_personas[stem] = {
                "name": name,
                "system_prompt": content,
                "description": description,
                "tool_allowlist": None,
                "begin_dialogs": [],
                "source": "file",
            }
        return len(self._file_personas)

    # ---------- CRUD ----------

    def list(self) -> list[dict]:
        merged = dict(self._file_personas)
        merged.update(self._db.get("personas", {}))
        return [
            {
                "id": pid,
                "name": p.get("name", pid),
                "description": p.get("description", ""),
                "tool_allowlist": p.get("tool_allowlist"),
                "source": p.get("source", "db"),
            }
            for pid, p in merged.items()
        ]

    def get(self, persona_id: str) -> dict | None:
        if persona_id in self._file_personas:
            return self._file_personas[persona_id]
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
            k: v for k, v in self._session_personas.items() if v.get("persona") != persona_id
        }
        return {"ok": True}

    # ---------- 会话切换 ----------

    def switch(self, session_id: str, persona_id: str | None) -> dict:
        if persona_id is None:
            self._session_personas.pop(session_id, None)
            return {"ok": True, "message": "已恢复默认人格"}
        if self.get(persona_id) is None:
            return {"error": f"人格不存在: {persona_id}"}
        self._session_personas[session_id] = {"persona": persona_id, "ts": time.time()}
        p = self.get(persona_id)
        return {"ok": True, "message": f"已切换到人格: {p.get('name', persona_id)}"}

    def _maybe_prune(self) -> None:
        """摊销清理:每 128 次访问清理超过 24h 未再切换的会话绑定。"""
        self._access_count += 1
        if self._access_count % 128 != 0:
            return
        now = time.time()
        stale = [sid for sid, e in self._session_personas.items() if now - e.get("ts", 0) > 86400]
        for sid in stale:
            self._session_personas.pop(sid, None)

    def get_prompt(self, session_id: str) -> str:
        self._maybe_prune()
        entry = self._session_personas.get(session_id)
        pid = entry.get("persona") if entry else None
        if pid:
            p = self.get(pid)
            if p and p.get("system_prompt"):
                return p["system_prompt"]
        return self._default_prompt

    def get_tool_allowlist(self, session_id: str) -> list[str] | None:
        self._maybe_prune()
        entry = self._session_personas.get(session_id)
        pid = entry.get("persona") if entry else None
        if pid:
            p = self.get(pid)
            if p:
                return p.get("tool_allowlist")
        return None
