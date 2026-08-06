"""AIGC 生成工具:图片/视频/语音生成。

生成器经 ToolContext.extra["generation"] 注入(由 main.py 构建 GenerationManager)。
产物返回本地路径,可继续调用 qq_send_image / qq_send_voice 发送到会话。
"""
from __future__ import annotations

from typing import Any

from .base import Tool, ToolContext


def _gen(ctx: ToolContext) -> Any:
    gen = ctx.extra.get("generation")
    if gen is None:
        raise RuntimeError("生成模块未启用")
    return gen


class ImageGenerateTool(Tool):
    name = "image_generate"
    description = "根据文字描述生成图片,返回图片文件路径(可配合 qq_send_image 发送)"
    parameters = {
        "type": "object",
        "properties": {
            "prompt": {"type": "string", "description": "图片内容描述(英文效果更佳)"},
            "size": {"type": "string", "description": "尺寸,如 1024x1024(默认)"},
        },
        "required": ["prompt"],
    }

    async def execute(self, ctx: ToolContext, prompt: str, size: str = "1024x1024") -> Any:
        try:
            gen = _gen(ctx)
            path = await gen.image.generate(prompt, size)
            return {"ok": True, "path": path, "hint": f"图片已生成: {path},可调用 qq_send_image 发送"}
        except RuntimeError as exc:
            return {"error": str(exc)}


class VideoGenerateTool(Tool):
    name = "video_generate"
    description = "根据文字描述生成视频(需配置 Runway API Key),返回视频文件路径"
    parameters = {
        "type": "object",
        "properties": {
            "prompt": {"type": "string", "description": "视频内容描述"},
            "image": {"type": "string", "description": "可选:输入图片URL或路径,做图生视频"},
            "duration": {"type": "integer", "description": "时长秒数,默认5"},
        },
        "required": ["prompt"],
    }

    async def execute(self, ctx: ToolContext, prompt: str, image: str = "", duration: int = 5) -> Any:
        try:
            gen = _gen(ctx)
            path = await gen.video.generate(prompt, image or None, int(duration or 5))
            return {"ok": True, "path": path, "hint": f"视频已生成: {path}"}
        except RuntimeError as exc:
            return {"error": str(exc)}


class TtsSpeakTool(Tool):
    name = "tts_speak"
    description = "文本转语音合成,返回音频文件路径(可配合 qq_send_voice 发送)"
    parameters = {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "要朗读的文本"},
            "voice": {"type": "string", "description": "可选语音,如 zh-CN-XiaoxiaoNeural"},
        },
        "required": ["text"],
    }

    async def execute(self, ctx: ToolContext, text: str, voice: str = "") -> Any:
        try:
            gen = _gen(ctx)
            path = await gen.audio.speak(text, voice or None)
            return {"ok": True, "path": path, "hint": f"语音已合成: {path},可调用 qq_send_voice 发送"}
        except RuntimeError as exc:
            return {"error": str(exc)}


class MixedMediaTool(Tool):
    name = "mixed_media"
    description = "混合媒体合成:一次生成多段 文本/图片/语音(如图文并茂的分享)"
    parameters = {
        "type": "object",
        "properties": {
            "segments": {
                "type": "array",
                "description": "段落列表,每段含 text/image_prompt/speak 字段",
                "items": {"type": "object"},
            },
        },
        "required": ["segments"],
    }

    async def execute(self, ctx: ToolContext, segments: list) -> Any:
        try:
            gen = _gen(ctx)
            result = await gen.mixed.compose(segments or [])
            return result
        except RuntimeError as exc:
            return {"error": str(exc)}
