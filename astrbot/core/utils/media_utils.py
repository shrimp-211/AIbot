"""媒体工具(精简兼容:本项目媒体处理在 src/agent/perception 与 adapter 中)。"""

from __future__ import annotations

import os

from astrbot.core import logger
from astrbot.core.utils.astrbot_path import get_astrbot_temp_path

MEDIA_MIME_EXTENSIONS: dict = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".amr": "audio/amr",
    ".silk": "audio/silk",
    ".mp4": "video/mp4",
    ".mkv": "video/x-matroska",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".json": "application/json",
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".zip": "application/zip",
}


def is_file_uri(s) -> bool:
    return isinstance(s, str) and s.startswith(("file://", "http://", "https://"))


def file_uri_to_path(s: str) -> str:
    return s


async def detect_image_mime_type_async(path: str) -> str:
    return MEDIA_MIME_EXTENSIONS.get(os.path.splitext(str(path))[1].lower(), "image/png")


class MediaResolver:
    """媒体解析器(精简:直接返回 url/path,媒体下载由本项目工具处理)。"""

    def __init__(self, url=None, media_type: str | None = None, *args, **kwargs) -> None:
        self.url = url
        self.media_type = media_type

    async def to_path(self) -> str:
        return str(self.url)

    async def to_base64(self) -> str | None:
        return None
