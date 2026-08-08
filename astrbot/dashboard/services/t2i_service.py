"""T2iService(本项目适配):文生图 HTML 模板管理。

模板为 HTML 文件,存储在 ``data/t2i_templates``;激活模板记录在 ``data/t2i_active.json``。
"""

from __future__ import annotations

import json
import os

from astrbot.core import logger
from astrbot.core.core_lifecycle import AstrBotCoreLifecycle
from astrbot.core.utils.astrbot_path import get_astrbot_data_path

T2I_DIR = os.path.join(get_astrbot_data_path(), "t2i_templates")
_ACTIVE_FILE = os.path.join(get_astrbot_data_path(), "t2i_active.json")


class T2iServiceError(Exception):
    pass


class T2iService:
    def __init__(self, core_lifecycle: AstrBotCoreLifecycle) -> None:
        self.core_lifecycle = core_lifecycle
        self.config = getattr(core_lifecycle, "astrbot_config", None)
        self.generation = getattr(core_lifecycle, "generation", None)

    def _template_path(self, name: str) -> str:
        safe = os.path.basename(name)
        return os.path.join(T2I_DIR, safe if safe.endswith(".html") else safe + ".html")

    def _read_active(self) -> str | None:
        try:
            if os.path.exists(_ACTIVE_FILE):
                with open(_ACTIVE_FILE, encoding="utf-8") as f:
                    return json.load(f).get("name") or None
        except Exception:
            pass
        return None

    async def list_templates(self) -> dict:
        """列出全部模板。"""
        os.makedirs(T2I_DIR, exist_ok=True)
        active = self._read_active()
        templates = []
        for f in sorted(os.listdir(T2I_DIR)):
            if not f.endswith(".html"):
                continue
            name = f[:-5]
            templates.append({"name": name, "is_active": name == active})
        return {"templates": templates, "total": len(templates)}

    async def create_template(self, name: str, content: str) -> dict:
        if not name or not name.strip():
            raise T2iServiceError("缺少模板名称")
        if not content or not content.strip():
            raise T2iServiceError("模板内容为空")
        os.makedirs(T2I_DIR, exist_ok=True)
        path = self._template_path(name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return {"name": os.path.basename(path)[:-5], "content": content, "is_active": False}

    async def set_active_template(self, name: str) -> dict:
        path = self._template_path(name)
        if not os.path.isfile(path):
            raise T2iServiceError("模板不存在", )
        os.makedirs(os.path.dirname(_ACTIVE_FILE), exist_ok=True)
        with open(_ACTIVE_FILE, "w", encoding="utf-8") as f:
            json.dump({"name": os.path.basename(path)[:-5]}, f, ensure_ascii=False)
        return {"name": os.path.basename(path)[:-5], "is_active": True}

    async def get_template(self, name: str) -> dict:
        path = self._template_path(name)
        if not os.path.isfile(path):
            raise T2iServiceError("模板不存在", )
        with open(path, encoding="utf-8") as f:
            content = f.read()
        base = os.path.basename(path)[:-5]
        return {"name": base, "content": content, "is_active": base == self._read_active()}

    async def get_active_template(self) -> dict:
        """返回当前激活模板(无激活时返回空模板)。"""
        name = self._read_active()
        if not name:
            return {"name": None, "content": "", "is_active": True}
        try:
            return await self.get_template(name)
        except T2iServiceError:
            return {"name": name, "content": "", "is_active": True}

    async def update_template(self, name: str, content: str) -> dict:
        path = self._template_path(name)
        if not os.path.isfile(path):
            raise T2iServiceError("模板不存在", )
        with open(path, "w", encoding="utf-8") as f:
            f.write(content or "")
        return {"name": os.path.basename(path)[:-5], "content": content or ""}

    async def delete_template(self, name: str) -> dict:
        path = self._template_path(name)
        if os.path.isfile(path):
            os.remove(path)
        return {"name": name, "deleted": True}
