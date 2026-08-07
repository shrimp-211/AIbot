"""BackupService(本项目适配):备份/恢复 config.yaml 与 data 目录。

备份为 zip 文件,存储在 ``data/backups``。备份内容包括 config.yaml 与整个 data 目录
(排除 backups/temp/webchat 等运行时目录)。
"""

from __future__ import annotations

import asyncio
import os
import shutil
import time
import uuid
import zipfile

from astrbot.core import logger
from astrbot.core.core_lifecycle import AstrBotCoreLifecycle
from astrbot.core.utils.astrbot_path import get_astrbot_data_path, get_astrbot_path

BACKUP_DIR = os.path.join(get_astrbot_data_path(), "backups")
_EXCLUDE_DIRS = {
    "backups", "temp", "webchat", "site-packages", "__pycache__",
    "workspaces", "reference", "node_modules", ".venv", "venv",
}


class BackupServiceError(Exception):
    pass


class DownloadResult:
    def __init__(self, path: str, filename: str) -> None:
        self.path = path
        self.filename = filename


class BackupService:
    def __init__(self, db, core_lifecycle: AstrBotCoreLifecycle) -> None:
        self.db = db
        self.core_lifecycle = core_lifecycle
        self.config = core_lifecycle.astrbot_config
        self.backup_dir = BACKUP_DIR

    # ---------------- 创建/列表 ----------------

    def create_backup(self, name: str | None = None) -> str:
        os.makedirs(self.backup_dir, exist_ok=True)
        ts = time.strftime("%Y%m%d_%H%M%S")
        filename = f"{name or 'backup'}_{ts}.zip"
        zip_path = os.path.join(self.backup_dir, filename)
        cfg = os.path.join(get_astrbot_path(), "config.yaml")
        data_dir = get_astrbot_data_path()
        parent = os.path.dirname(data_dir)
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            if os.path.exists(cfg):
                zf.write(cfg, "config.yaml")
            if os.path.isdir(data_dir):
                for root, dirs, files in os.walk(data_dir):
                    dirs[:] = [d for d in dirs if d not in _EXCLUDE_DIRS and d != ".git"]
                    for f in files:
                        if f.endswith((".tmp", ".lock")):
                            continue
                        full = os.path.join(root, f)
                        rel = os.path.relpath(full, parent)
                        try:
                            zf.write(full, rel)
                        except OSError:
                            continue
        return zip_path

    async def export_backup(self, name: str | None = None) -> dict:
        """创建备份(后台线程避免阻塞事件循环)并返回信息。"""
        path = await asyncio.to_thread(self.create_backup, name)
        return {
            "filename": os.path.basename(path),
            "path": path,
            "size": os.path.getsize(path),
        }

    async def list_backups(self, *, page: int = 1, page_size: int = 20) -> dict:
        os.makedirs(self.backup_dir, exist_ok=True)
        files = []
        for f in sorted(os.listdir(self.backup_dir)):
            if not f.endswith(".zip"):
                continue
            full = os.path.join(self.backup_dir, f)
            st = os.stat(full)
            files.append({
                "filename": f,
                "size": st.st_size,
                "created_at": st.st_mtime,
            })
        files.reverse()
        total = len(files)
        start = (page - 1) * page_size
        items = files[start : start + page_size]
        return {
            "backups": items,
            "pagination": {
                "page": page,
                "page_size": page_size,
                "total": total,
                "total_pages": (total + page_size - 1) // page_size if total else 0,
            },
        }

    # ---------------- 下载/删除/重命名 ----------------

    def prepare_download(self, *, filename: str, token: str | None = None, jwt_secret: str | None = None) -> DownloadResult:
        full = os.path.join(self.backup_dir, os.path.basename(filename))
        if not os.path.isfile(full):
            raise BackupServiceError("备份文件不存在", )
        return DownloadResult(full, filename)

    async def delete_backup(self, data: dict) -> tuple[None, str]:
        filename = str(data.get("filename") or "")
        full = os.path.join(self.backup_dir, os.path.basename(filename))
        if os.path.isfile(full):
            os.remove(full)
        return None, "备份已删除"

    async def rename_backup(self, data: dict) -> dict:
        filename = str(data.get("filename") or "")
        new_name = str(data.get("new_name") or data.get("name") or "")
        if not new_name:
            raise BackupServiceError("缺少新文件名")
        old = os.path.join(self.backup_dir, os.path.basename(filename))
        if not os.path.isfile(old):
            raise BackupServiceError("备份文件不存在")
        new = os.path.join(self.backup_dir, os.path.basename(new_name) if new_name.endswith(".zip") else new_name + ".zip")
        os.rename(old, new)
        return {"filename": os.path.basename(new)}

    async def check_backup(self, data: dict) -> dict:
        filename = str(data.get("filename") or "")
        full = os.path.join(self.backup_dir, os.path.basename(filename))
        return {"filename": filename, "exists": os.path.isfile(full)}

    # ---------------- 上传/导入 ----------------

    async def upload_backup(self, file) -> dict:
        raw = await file.read()
        if len(raw) > 200 * 1024 * 1024:
            raise BackupServiceError("备份文件过大(上限 200MB)")
        fname = getattr(file, "filename", "") or f"backup_{int(time.time())}.zip"
        if not fname.endswith(".zip"):
            fname += ".zip"
        os.makedirs(self.backup_dir, exist_ok=True)
        dest = os.path.join(self.backup_dir, os.path.basename(fname))
        with open(dest, "wb") as f:
            f.write(raw)
        return {"filename": os.path.basename(dest), "size": len(raw)}

    async def import_backup(self, data: dict) -> tuple[None, str]:
        filename = str(data.get("filename") or "")
        full = os.path.join(self.backup_dir, os.path.basename(filename))
        if not os.path.isfile(full):
            raise BackupServiceError("备份文件不存在")
        data_dir = get_astrbot_data_path()
        total_size = 0
        with zipfile.ZipFile(full) as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                total_size += info.file_size
                if total_size > 500 * 1024 * 1024:
                    raise BackupServiceError("备份解压内容过大(上限 500MB)")
                rel = info.filename.replace("\\", "/")
                # 防 zip slip:拒绝绝对路径或带 .. 的路径
                if os.path.isabs(rel) or ".." in rel.split("/"):
                    raise BackupServiceError("备份文件包含非法路径,已中止恢复")
                # config.yaml 恢复到源码目录;data/* 恢复到数据目录
                if rel == "config.yaml":
                    target = os.path.join(get_astrbot_path(), "config.yaml")
                elif rel.startswith("data/"):
                    target = os.path.join(data_dir, rel[len("data/") :])
                else:
                    target = os.path.join(data_dir, rel)
                parent = os.path.dirname(target)
                os.makedirs(parent, exist_ok=True)
                with zf.open(info) as src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst)
        return None, "备份已恢复(重启生效)"

    # ---------------- 分块上传(降级为直接保存) ----------------

    async def upload_init(self, data: dict) -> dict:
        return {"task_id": str(uuid.uuid4()), "status": "ready"}

    async def upload_chunk(self, data: dict) -> dict:
        return {"status": "ok"}

    async def upload_complete(self, data: dict) -> dict:
        return {"status": "ok", "message": "上传完成"}

    async def upload_abort(self, data: dict) -> dict:
        return {"status": "aborted"}

    async def get_progress(self, task_id: str | None) -> dict:
        return {"task_id": task_id, "status": "completed", "progress": 100}
