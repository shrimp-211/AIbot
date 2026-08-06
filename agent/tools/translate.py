"""翻译工具:Google 翻译公共接口(gtx),自动检测语言。

目标域名固定(translate.googleapis.com,公网),参数仅 q/tl,
不涉及用户提供的 URL,无 SSRF 风险。失败时返回通用错误信息。
"""
from __future__ import annotations

import asyncio
from typing import Any

import aiohttp

from .base import Tool, ToolContext

_ENDPOINT = "https://translate.googleapis.com/translate_a/single"
_SUPPORTED = {
    "zh": "zh-CN",
    "zh-CN": "zh-CN",
    "zh-TW": "zh-TW",
    "en": "en",
    "ja": "ja",
    "ko": "ko",
    "fr": "fr",
    "de": "de",
    "es": "es",
    "ru": "ru",
    "auto": "auto",
}


class TranslateTool(Tool):
    name = "translate"
    description = "翻译文本(自动检测源语言,可指定目标语言 zh-CN/en/ja/ko/fr/de/es/ru)"
    parameters = {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "要翻译的文本"},
            "target": {
                "type": "string",
                "description": "目标语言代码,默认 zh-CN",
            },
        },
        "required": ["text"],
    }

    async def execute(self, ctx: ToolContext, text: str, target: str = "zh-CN") -> Any:
        if not text or len(text) > 4000:
            return {"error": "翻译文本不能为空且长度不超过 4000 字符"}
        tl = _SUPPORTED.get(str(target or ""), str(target or "zh-CN"))
        params = {"client": "gtx", "sl": "auto", "tl": tl, "dt": "t", "q": text}
        try:
            timeout = aiohttp.ClientTimeout(total=15)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(_ENDPOINT, params=params) as resp:
                    if resp.status != 200:
                        return {"error": f"翻译服务响应异常: {resp.status}"}
                    data = await resp.json()
        except (aiohttp.ClientError, asyncio.TimeoutError):
            return {"error": "翻译服务暂时不可用,请稍后再试"}

        try:
            segments = data[0] or []
            translated = "".join(seg[0] for seg in segments if seg and seg[0])
            src_lang = data[2] if len(data) > 2 else ""
        except (IndexError, TypeError):
            return {"error": "翻译服务返回格式异常"}

        if not translated:
            return {"error": "翻译结果为空"}
        return {"source_lang": src_lang or "unknown", "target": tl, "translated": translated}
