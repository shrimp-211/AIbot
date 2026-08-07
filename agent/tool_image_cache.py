"""工具图片缓存:工具返回的图片复制到缓存目录,供多模态模型在上下文中查看。

参照 AstrBot tool_image_cache:工具生成/返回图片后,引擎把图片注入一条用户消息,
让支持图像输入的 LLM 直接看到画面并据此继续。
"""
from __future__ import annotations

import base64
import shutil
import time
from pathlib import Path


class ToolImageCache:
    """本地图片缓存(LRU 数量 + TTL 清理)。"""

    def __init__(self, data_dir: str = "data/tool_images", max_files: int = 200, ttl: int = 3600):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.max_files = max(10, int(max_files))
        self.ttl = int(ttl)

    def save_image(self, image_path: str, tool_call_id: str, tool_name: str, index: int = 0) -> dict | None:
        """把本地图片复制进缓存,返回 {file_path, mime_type, tool_name}。"""
        src = Path(image_path)
        if not src.is_file():
            return None
        ext = src.suffix.lower()
        mime = {
            ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
            ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp",
        }.get(ext, "image/png")
        dest = self.data_dir / f"{tool_name}_{tool_call_id}_{index}{ext or '.png'}"
        try:
            shutil.copyfile(src, dest)
        except OSError:
            return None
        self._gc()
        return {"file_path": str(dest), "mime_type": mime, "tool_name": tool_name}

    def get_image_base64(self, path: str) -> tuple[str | None, str | None]:
        """读取缓存图片为 base64,返回 (base64, mime)。"""
        p = Path(path)
        if not p.is_file():
            return None, None
        mime = {
            ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
            ".gif": "image/gif", ".webp": "image/webp",
        }.get(p.suffix.lower(), "image/png")
        try:
            b64 = base64.b64encode(p.read_bytes()).decode()
            return b64, mime
        except OSError:
            return None, None

    def _gc(self) -> None:
        """清理过期文件与超量文件(按 mtime 最旧优先)。"""
        now = time.time()
        try:
            files = [p for p in self.data_dir.iterdir() if p.is_file()]
        except OSError:
            return
        expired = [p for p in files if now - p.stat().st_mtime > self.ttl]
        for p in expired:
            p.unlink(missing_ok=True)
        files = [p for p in self.data_dir.iterdir() if p.is_file()] if expired else files
        if len(files) > self.max_files:
            files.sort(key=lambda p: p.stat().st_mtime)
            for p in files[: len(files) - self.max_files]:
                p.unlink(missing_ok=True)
