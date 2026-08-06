"""翻译插件:调用 Google 免费翻译接口(gtx),无需 API Key。

参考 nonebot-plugin-translator。命令:
- /翻译 <文本>         → 自动识别语种,翻译为中文
- /翻译 <目标代码> <文本> → 翻译为目标语种(如 en / ja / ko)
- /翻译 反向 <文本>     → 中文 → 英文
"""
from __future__ import annotations

import aiohttp

from src.adapter.event import AgentEvent

_API = "https://translate.googleapis.com/translate_a/single"
_LANG_ALIAS = {
    "中文": "zh", "英文": "en", "英语": "en", "日语": "ja", "韩语": "ko",
    "法语": "fr", "德语": "de", "俄语": "ru", "西语": "es", "西班牙语": "es",
}
_DEFAULT_TO = "zh"


def _normalize_lang(token: str) -> str | None:
    token = token.strip().lower()
    if token in _LANG_ALIAS:
        return _LANG_ALIAS[token]
    if len(token) == 2 and token.isalpha():
        return token
    return None


async def _translate(text: str, to: str) -> str:
    params = {"client": "gtx", "sl": "auto", "tl": to, "dt": "t", "q": text}
    async with aiohttp.ClientSession() as session:
        async with session.get(_API, params=params, timeout=15) as resp:
            data = await resp.json(content_type=None)
    # 返回结构:[[["译文","原文",...],...], "原文", ...]
    segs = data.get(0) if isinstance(data, list) and data else []
    out = "".join(seg[0] for seg in segs if isinstance(seg, list) and seg and seg[0])
    return out or "翻译结果为空。"


def setup(registry) -> None:
    @registry.command("翻译", permission_level=1)
    async def translate(event: AgentEvent):
        arg = (event.state.get("command_arg") or "").strip()
        if not arg:
            await event.reply("用法:\n/翻译 <文本>\n/翻译 <目标代码> <文本>\n/翻译 反向 <文本>")
            return None
        to = _DEFAULT_TO
        text = arg
        parts = arg.split(None, 1)
        lang = _normalize_lang(parts[0])
        if lang and len(parts) > 1:
            to = lang
            text = parts[1]
        elif parts[0] in ("反向", "back"):
            to = "en"
            text = parts[1] if len(parts) > 1 else ""
            if not text:
                await event.reply("用法: /翻译 反向 <中文文本>")
                return None
        try:
            result = await _translate(text, to)
        except Exception:  # noqa: BLE001
            result = "翻译服务暂时不可用,请稍后再试。"
        await event.reply(f"🌐 [{to}] {result}")
        return None
