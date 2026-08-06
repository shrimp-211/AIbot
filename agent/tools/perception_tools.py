"""多模态感知工具:图片理解/OCR/语音转写/视频摘要/文档解析/通用分析。

媒体文件支持 URL 或本地路径:URL 先下载到临时目录再交给感知器。
感知器经 ToolContext.extra["perception"] 注入(由 main.py 构建 PerceptionManager)。
"""
from __future__ import annotations

import tempfile
import time
from pathlib import Path
from typing import Any

from .base import Tool, ToolContext

_MEDIA_EXT_BY_KIND = {
    "image": (".jpg", ".png", ".gif", ".webp", ".jpeg", ".bmp"),
    "audio": (".mp3", ".wav", ".ogg", ".m4a", ".flac"),
    "video": (".mp4", ".mkv", ".webm", ".mov"),
    "document": (".pdf", ".txt", ".md", ".docx", ".pptx", ".xlsx", ".epub", ".html"),
    "code": (".py", ".js", ".ts", ".java", ".c", ".go", ".rs"),
    "unknown": (".bin",),
}
_MEDIA_FALLBACK_EXT = {k: v[0] for k, v in _MEDIA_EXT_BY_KIND.items()}


async def _resolve_media(path_or_url: str, kind: str) -> str:
    """URL 下载到临时文件,本地路径直接返回;解析失败抛 RuntimeError。"""
    if not path_or_url:
        raise RuntimeError(f"未提供{kind}文件")
    if path_or_url.startswith(("http://", "https://")):
        import httpx

        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
            resp = await client.get(path_or_url)
            resp.raise_for_status()
        suffix = Path(path_or_url).suffix.lower()
        allowed = _MEDIA_EXT_BY_KIND.get(kind, ())
        if suffix not in allowed:
            suffix = _MEDIA_FALLBACK_EXT.get(kind, ".bin")
        tmp = Path(tempfile.gettempdir()) / f"qqbot_{kind}_{int(time.time() * 1000)}{suffix}"
        tmp.write_bytes(resp.content)
        return str(tmp)
    if Path(path_or_url).is_file():
        return path_or_url
    raise RuntimeError(f"{kind}文件不存在: {path_or_url}")


class VisionAnalyzeTool(Tool):
    name = "vision_analyze"
    description = "分析图片内容(支持URL或本地路径),可提问获得针对性回答"
    requires_modal = "image"
    parameters = {
        "type": "object",
        "properties": {
            "image": {"type": "string", "description": "图片URL或本地路径"},
            "question": {"type": "string", "description": "要问的问题,默认'描述图片'"},
        },
        "required": ["image"],
    }

    async def execute(self, ctx: ToolContext, image: str, question: str = "") -> Any:
        perception = ctx.extra.get("perception")
        if perception is None:
            return {"error": "感知模块未启用"}
        try:
            path = await _resolve_media(image, "image")
        except RuntimeError as exc:
            return {"error": str(exc)}
        try:
            result = await perception.image.describe(
                path, question or "请详细描述这张图片的内容。", perception._llm_analyzer
            )
            return {"content": result}
        except RuntimeError as exc:
            return {"error": str(exc)}


class OcrTool(Tool):
    name = "ocr_extract"
    description = "从图片中提取文字(OCR),返回识别出的文本"
    requires_modal = "image"
    parameters = {
        "type": "object",
        "properties": {
            "image": {"type": "string", "description": "图片URL或本地路径"},
            "lang": {"type": "string", "description": "识别语言,默认 chi_sim+eng"},
        },
        "required": ["image"],
    }

    async def execute(self, ctx: ToolContext, image: str, lang: str = "chi_sim+eng") -> Any:
        perception = ctx.extra.get("perception")
        if perception is None:
            return {"error": "感知模块未启用"}
        try:
            path = await _resolve_media(image, "image")
        except RuntimeError as exc:
            return {"error": str(exc)}
        try:
            text = await perception.image.ocr(path, lang=lang)
            return {"content": text or "(未识别到文字)", "char_count": len(text)}
        except RuntimeError as exc:
            return {"error": str(exc)}


class AudioTranscribeTool(Tool):
    name = "audio_transcribe"
    description = "将语音/音频转为文字(需配置 STT Provider,默认 Whisper)"
    requires_modal = "audio"
    parameters = {
        "type": "object",
        "properties": {
            "audio": {"type": "string", "description": "音频文件URL或本地路径"},
        },
        "required": ["audio"],
    }

    async def execute(self, ctx: ToolContext, audio: str) -> Any:
        perception = ctx.extra.get("perception")
        if perception is None:
            return {"error": "感知模块未启用"}
        try:
            path = await _resolve_media(audio, "audio")
        except RuntimeError as exc:
            return {"error": str(exc)}
        try:
            text = await perception.audio.transcribe(path)
            return {"content": text or "(未识别到语音)"}
        except RuntimeError as exc:
            return {"error": str(exc)}


class VideoSummarizeTool(Tool):
    name = "video_summarize"
    description = "分析视频内容:抽取关键帧并生成画面描述(需多模态 LLM)"
    requires_modal = "video"
    parameters = {
        "type": "object",
        "properties": {
            "video": {"type": "string", "description": "视频文件URL或本地路径"},
        },
        "required": ["video"],
    }

    async def execute(self, ctx: ToolContext, video: str) -> Any:
        perception = ctx.extra.get("perception")
        if perception is None:
            return {"error": "感知模块未启用"}
        try:
            path = await _resolve_media(video, "video")
        except RuntimeError as exc:
            return {"error": str(exc)}
        try:
            result = await perception.video.summarize(path, perception._llm_analyzer)
            return {"content": result}
        except RuntimeError as exc:
            return {"error": str(exc)}


class DocumentParseTool(Tool):
    name = "document_parse"
    description = "解析文档内容(PDF/Word/PPT/Excel/EPUB/纯文本),返回文本"
    parameters = {
        "type": "object",
        "properties": {
            "file": {"type": "string", "description": "文档URL或本地路径"},
        },
        "required": ["file"],
    }

    async def execute(self, ctx: ToolContext, file: str) -> Any:
        perception = ctx.extra.get("perception")
        if perception is None:
            return {"error": "感知模块未启用"}
        try:
            path = await _resolve_media(file, "document")
        except RuntimeError as exc:
            return {"error": str(exc)}
        try:
            doc = await perception.document.parse(path)
            return {
                "content": doc["content"][:4000],
                "char_count": doc["char_count"],
                "type": doc["type"],
            }
        except RuntimeError as exc:
            return {"error": str(exc)}


class MediaAnalyzeTool(Tool):
    name = "media_analyze"
    description = "通用媒体分析:按文件类型自动路由(图片/音频/视频/文档/代码),返回理解结果"
    parameters = {
        "type": "object",
        "properties": {
            "file": {"type": "string", "description": "媒体文件URL或本地路径"},
            "question": {"type": "string", "description": "可选的分析问题"},
        },
        "required": ["file"],
    }

    async def execute(self, ctx: ToolContext, file: str, question: str = "") -> Any:
        perception = ctx.extra.get("perception")
        if perception is None:
            return {"error": "感知模块未启用"}
        from ..perception import classify_media

        kind = classify_media(file)
        if kind == "unknown" and file.startswith(("http://", "https://")):
            kind = "image"  # 无扩展名 URL 默认按图片处理
        try:
            path = await _resolve_media(file, kind if kind != "unknown" else "document")
        except RuntimeError as exc:
            return {"error": str(exc)}
        try:
            result = await perception.analyze(path, question)
            return {"kind": result["kind"], "result": str(result["result"])[:4000]}
        except RuntimeError as exc:
            return {"error": str(exc)}
