"""SkillsService(本项目适配):映射 src/agent/skills 的技能系统。

技能为 ``SKILL.md`` 文件:内置在 ``src/skills``,用户技能在 ``data/skills``。
- 列表/详情/上传/删除/更新真实操作技能文件
- 激活状态持久化到 ``data/skills_active.json``(WebUI 全局标记)
- Neo 技能发布(AstrBot 特有)降级为空列表
"""

from __future__ import annotations

import json
import os
import shutil
import zipfile

from astrbot.core import logger
from astrbot.core.core_lifecycle import AstrBotCoreLifecycle
from astrbot.core.utils.astrbot_path import get_astrbot_data_path, get_astrbot_path

BUILTIN_SKILLS_DIR = os.path.join(get_astrbot_path(), "skills")
USER_SKILLS_DIR = os.path.join(get_astrbot_data_path(), "skills")
_ACTIVE_FILE = os.path.join(get_astrbot_data_path(), "skills_active.json")


class SkillsServiceError(Exception):
    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


class SkillsOperationResult:
    def __init__(self, ok: bool = True, data=None, message: str = "") -> None:
        self.ok = ok
        self.data = data
        self.message = message


class SkillArchive:
    def __init__(self, path: str, filename: str) -> None:
        self.path = path
        self.filename = filename


class SkillsService:
    def __init__(self, core_lifecycle: AstrBotCoreLifecycle) -> None:
        self.core_lifecycle = core_lifecycle
        self.skill_registry = getattr(core_lifecycle, "skills", None)

    # ---------------- 路径工具 ----------------

    def _skill_dirs(self) -> list[str]:
        return [BUILTIN_SKILLS_DIR, USER_SKILLS_DIR]

    def _skill_file(self, name: str) -> str | None:
        # 防路径穿越:技能名仅取 basename
        name = os.path.basename(str(name or "").strip())
        if not name:
            return None
        for d in self._skill_dirs():
            for candidate in (f"{name}.md", name):
                p = os.path.join(d, candidate)
                if os.path.isfile(p):
                    return p
        return None

    def _read_active(self) -> str | None:
        try:
            if os.path.exists(_ACTIVE_FILE):
                with open(_ACTIVE_FILE, encoding="utf-8") as f:
                    return json.load(f).get("skill") or None
        except Exception:
            pass
        return None

    def _write_active(self, name: str | None) -> None:
        try:
            os.makedirs(os.path.dirname(_ACTIVE_FILE), exist_ok=True)
            with open(_ACTIVE_FILE, "w", encoding="utf-8") as f:
                json.dump({"skill": name}, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _parse_desc(self, text: str) -> str:
        for line in (text or "").splitlines():
            line = line.strip()
            if line.startswith("description:"):
                return line.split(":", 1)[1].strip().strip('"').strip("'")
            if line and not line.startswith(("#", "---", "name:")):
                return line[:100]
        return ""

    # ---------------- 列表 ----------------

    async def get_skills(self) -> SkillsOperationResult:
        active = self._read_active()
        items = []
        seen: set[str] = set()
        for d in self._skill_dirs():
            if not os.path.isdir(d):
                continue
            for f in sorted(os.listdir(d)):
                if not f.endswith(".md"):
                    continue
                name = f[:-3]
                if name in seen:
                    continue
                seen.add(name)
                path = os.path.join(d, f)
                try:
                    with open(path, encoding="utf-8") as fh:
                        content = fh.read()
                except OSError:
                    content = ""
                skill = self.skill_registry.get(name) if self.skill_registry else None
                items.append({
                    "name": name,
                    "skill_name": name,
                    "description": (skill.description if skill else self._parse_desc(content)),
                    "content": content,
                    "path": path,
                    "source": "builtin" if d == BUILTIN_SKILLS_DIR else "user",
                    "active": name == active,
                    "enabled": True,
                    "tools": skill.tools if skill else None,
                })
        return SkillsOperationResult(ok=True, data={"skills": items})

    # ---------------- 上传/删除/更新 ----------------

    async def upload_skill(self, file) -> SkillsOperationResult:
        raw = await file.read()
        if len(raw) > 5 * 1024 * 1024:
            raise SkillsServiceError("技能文件过大(上限 5MB)")
        fname = os.path.basename(getattr(file, "filename", "") or "SKILL.md")
        if fname.endswith(".zip"):
            return await self._upload_zip(raw)
        if not fname.endswith(".md"):
            fname = fname + ".md"
        return self._write_skill_file(fname, raw.decode("utf-8", errors="replace"))

    async def batch_upload_skills(self, files) -> SkillsOperationResult:
        names = []
        for f in files:
            raw = await f.read()
            fname = getattr(f, "filename", "") or "SKILL.md"
            if fname.endswith(".zip"):
                await self._upload_zip(raw)
                continue
            if not fname.endswith(".md"):
                fname = fname + ".md"
            result = self._write_skill_file(fname, raw.decode("utf-8", errors="replace"))
            if result.ok:
                names.append(fname[:-3])
        return SkillsOperationResult(ok=True, data={"uploaded": names}, message="上传成功")

    async def _upload_zip(self, raw: bytes) -> SkillsOperationResult:
        if len(raw) > 50 * 1024 * 1024:
            raise SkillsServiceError("ZIP 文件过大(上限 50MB)")
        os.makedirs(USER_SKILLS_DIR, exist_ok=True)
        zip_path = os.path.join(USER_SKILLS_DIR, "_upload.zip")
        with open(zip_path, "wb") as f:
            f.write(raw)
        imported = []
        total = 0
        try:
            with zipfile.ZipFile(zip_path) as zf:
                for info in zf.infolist():
                    if info.is_dir():
                        continue
                    total += info.file_size
                    if total > 200 * 1024 * 1024:
                        raise SkillsServiceError("ZIP 解压内容过大(上限 200MB)")
                    base = os.path.basename(info.filename)
                    if base.endswith(".md"):
                        dest = os.path.join(USER_SKILLS_DIR, base)
                        with zf.open(info) as src, open(dest, "wb") as dst:
                            shutil.copyfileobj(src, dst)
                        imported.append(base[:-3])
        except zipfile.BadZipFile as exc:
            raise SkillsServiceError(f"无效的 ZIP 文件: {exc}") from exc
        finally:
            if os.path.exists(zip_path):
                os.remove(zip_path)
        return SkillsOperationResult(ok=True, data={"uploaded": imported}, message="上传成功")

    def _write_skill_file(self, filename: str, content: str) -> SkillsOperationResult:
        os.makedirs(USER_SKILLS_DIR, exist_ok=True)
        dest = os.path.join(USER_SKILLS_DIR, filename)
        with open(dest, "w", encoding="utf-8") as f:
            f.write(content)
        return SkillsOperationResult(ok=True, data={"name": filename[:-3]}, message="上传成功")

    async def update_skill(self, data: dict) -> SkillsOperationResult:
        name = str(data.get("name") or "").strip()
        if not name:
            raise SkillsServiceError("缺少技能名")
        if self._skill_file(name) is None:
            raise SkillsServiceError(f"技能不存在: {name}", status_code=404)
        if data.get("active"):
            self._write_active(name)
        elif "active" in data and data.get("active") is False:
            if self._read_active() == name:
                self._write_active(None)
        return SkillsOperationResult(
            ok=True, data={"name": name, "active": bool(data.get("active"))}, message="技能已更新"
        )

    async def delete_skill(self, data: dict) -> SkillsOperationResult:
        name = str(data.get("name") or "").strip()
        if not name:
            raise SkillsServiceError("缺少技能名")
        path = self._skill_file(name)
        if path is None:
            raise SkillsServiceError(f"技能不存在: {name}", status_code=404)
        if path.startswith(BUILTIN_SKILLS_DIR):
            raise SkillsServiceError("内置技能不可删除", status_code=400)
        os.remove(path)
        if self._read_active() == name:
            self._write_active(None)
        return SkillsOperationResult(ok=True, data={"name": name}, message="技能已删除")

    # ---------------- 文件管理 ----------------

    def prepare_skill_archive(self, name: str) -> SkillArchive:
        path = self._skill_file(name)
        if path is None:
            raise SkillsServiceError(f"技能不存在: {name}", status_code=404)
        tmp_dir = os.path.join(get_astrbot_data_path(), "temp", "skills")
        os.makedirs(tmp_dir, exist_ok=True)
        archive_path = os.path.join(tmp_dir, f"{name}.zip")
        with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.write(path, arcname="SKILL.md")
        return SkillArchive(archive_path, f"{name}.zip")

    async def list_skill_files(self, skill_name: str, path: str = "") -> SkillsOperationResult:
        base = self._skill_file(skill_name)
        if base is None:
            raise SkillsServiceError(f"技能不存在: {skill_name}", status_code=404)
        skill_dir = os.path.dirname(base)
        files = []
        for root, _dirs, names in os.walk(skill_dir):
            for n in sorted(names):
                full = os.path.join(root, n)
                rel = os.path.relpath(full, skill_dir)
                files.append({"name": n, "path": rel, "size": os.path.getsize(full)})
        return SkillsOperationResult(ok=True, data={"files": files})

    async def get_skill_file(self, skill_name: str, path: str) -> SkillsOperationResult:
        base = self._skill_file(skill_name)
        if base is None:
            raise SkillsServiceError(f"技能不存在: {skill_name}", status_code=404)
        if not path or path in (".", "SKILL.md"):
            target = base
        else:
            target = os.path.join(os.path.dirname(base), os.path.basename(path))
        if not os.path.isfile(target):
            raise SkillsServiceError("技能文件不存在", status_code=404)
        with open(target, encoding="utf-8") as f:
            content = f.read()
        return SkillsOperationResult(ok=True, data={"name": skill_name, "path": target, "content": content})

    async def update_skill_file(self, data: dict) -> SkillsOperationResult:
        name = os.path.basename(str(data.get("name") or "").strip())
        path = os.path.basename(str(data.get("path") or "").strip() or "SKILL.md")
        content = data.get("content") or ""
        base = self._skill_file(name)
        if base is None:
            os.makedirs(USER_SKILLS_DIR, exist_ok=True)
            base = os.path.join(USER_SKILLS_DIR, f"{name}.md")
        if path not in (".", "SKILL.md"):
            base = os.path.join(os.path.dirname(base), os.path.basename(path))
        os.makedirs(os.path.dirname(base), exist_ok=True)
        with open(base, "w", encoding="utf-8") as f:
            f.write(content)
        return SkillsOperationResult(ok=True, data={"name": name}, message="技能已保存")

    # ---------------- Neo 技能(AstrBot 特有,本项目降级) ----------------

    async def get_neo_candidates(self, params: dict | None = None) -> SkillsOperationResult:
        return SkillsOperationResult(ok=True, data={"candidates": [], "total": 0})

    async def get_neo_releases(self, params: dict | None = None) -> SkillsOperationResult:
        return SkillsOperationResult(ok=True, data={"releases": [], "total": 0})

    async def get_neo_payload(self, params: dict | None = None) -> SkillsOperationResult:
        return SkillsOperationResult(ok=True, data={"payload": None, "staged": None})

    async def evaluate_neo_candidate(self, data: dict) -> SkillsOperationResult:
        raise SkillsServiceError("本项目不支持 Neo 技能市场", status_code=501)

    async def promote_neo_candidate(self, data: dict) -> SkillsOperationResult:
        raise SkillsServiceError("本项目不支持 Neo 技能市场", status_code=501)

    async def rollback_neo_release(self, data: dict) -> SkillsOperationResult:
        raise SkillsServiceError("本项目不支持 Neo 技能市场", status_code=501)

    async def sync_neo_release(self, data: dict) -> SkillsOperationResult:
        raise SkillsServiceError("本项目不支持 Neo 技能市场", status_code=501)

    async def delete_neo_candidate(self, data: dict) -> SkillsOperationResult:
        return SkillsOperationResult(ok=True, data={}, message="已删除")

    async def delete_neo_release(self, data: dict) -> SkillsOperationResult:
        return SkillsOperationResult(ok=True, data={}, message="已删除")
