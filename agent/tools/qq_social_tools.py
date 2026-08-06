"""QQ 社交/富媒体工具:戳一戳、表情回应、骰子、音乐分享、图片 OCR。

底层走 NapCat/go-cqhttp 扩展 API(adapter/onebot_v11.py + qq_rich_media.py)。
适配器不支持时返回明确提示(适配度保障:不崩溃,告知能力边界)。
"""
from __future__ import annotations

from typing import Any

from ...adapter.qq_rich_media import cq_dice, cq_music
from .base import Tool, ToolContext


def _safe_call(fn, *, fallback: str) -> Any:
    """包装适配器调用:不支持/未连接时返回清晰提示而非抛错。"""
    try:
        return fn()
    except NotImplementedError:
        return {"error": fallback}
    except AttributeError:
        return {"error": fallback}
    except ConnectionError:
        return {"error": "OneBot WebSocket 未连接"}
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}


class QqSendPokeTool(Tool):
    name = "qq_send_poke"
    description = "戳一戳群成员或好友(NapCat 扩展),活跃群气氛"
    parameters = {
        "type": "object",
        "properties": {
            "user_id": {"type": "string", "description": "目标QQ号(留空=戳自己触发者)"},
            "group_id": {"type": "string", "description": "群号(留空=私聊戳好友)"},
        },
    }

    async def execute(self, ctx: ToolContext, user_id: str = "", group_id: str = "") -> Any:
        adapter = ctx.adapter
        target = user_id or str(ctx.event.user_id)
        if group_id or ctx.event.message_type == "group":
            gid = group_id or str(ctx.event.group_id or "")
            return _safe_call(
                lambda: adapter.group_poke(gid, target),
                fallback="当前平台不支持群戳一戳(NapCat 专属 API)",
            )
        return _safe_call(
            lambda: adapter.friend_poke(target),
            fallback="当前平台不支持好友戳一戳(NapCat 专属 API)",
        )


class QqSendEmojiTool(Tool):
    name = "qq_send_emoji"
    description = "对指定消息添加 QQ 表情回应(NapCat 扩展),如爱心/赞/大笑"
    parameters = {
        "type": "object",
        "properties": {
            "message_id": {"type": "integer", "description": "要回应的消息ID(默认回应触发者消息)"},
            "emoji_id": {"type": "integer", "description": "表情ID:307爱心/322赞/21大笑/6再见"},
        },
    }

    async def execute(self, ctx: ToolContext, message_id: int = 0, emoji_id: int = 307) -> Any:
        adapter = ctx.adapter
        mid = message_id or getattr(ctx.event, "message_id", 0) or 0
        if not mid:
            return {"error": "无法定位要回应的消息"}
        return _safe_call(
            lambda: adapter.set_msg_emoji_like(int(mid), int(emoji_id)),
            fallback="当前平台不支持表情回应(NapCat 专属 API)",
        )


class QqSendDiceTool(Tool):
    name = "qq_send_dice"
    description = "发送一个骰子到当前会话(客户端显示 1-6 随机)"
    parameters = {"type": "object", "properties": {}}

    async def execute(self, ctx: ToolContext, **kwargs: Any) -> Any:
        await ctx.adapter.send_message(ctx.event, cq_dice())
        return {"ok": True, "message": "骰子已发送"}


class QqSendMusicTool(Tool):
    name = "qq_send_music"
    description = "发送音乐分享到当前会话(网易云/QQ音乐/自定义)"
    parameters = {
        "type": "object",
        "properties": {
            "kind": {"type": "string", "description": "163(网易云) | qq(QQ音乐) | custom"},
            "music_id": {"type": "string", "description": "歌曲ID(kind=163/qq 时)"},
            "title": {"type": "string", "description": "自定义歌曲标题(kind=custom 时)"},
            "url": {"type": "string", "description": "自定义歌曲链接(kind=custom 时)"},
        },
        "required": ["kind"],
    }

    async def execute(self, ctx: ToolContext, kind: str, music_id: str = "", title: str = "", url: str = "") -> Any:
        try:
            cq = cq_music(kind, music_id, title=title, url=url)
        except ValueError as exc:
            return {"error": str(exc)}
        await ctx.adapter.send_message(ctx.event, cq)
        return {"ok": True, "message": "音乐分享已发送"}


class QqOcrTool(Tool):
    name = "qq_ocr"
    description = "用 NapCat 内置 OCR 识别图片文字(无需本地 Tesseract),返回文本"
    parameters = {
        "type": "object",
        "properties": {
            "image": {"type": "string", "description": "图片URL或本地路径"},
        },
        "required": ["image"],
    }

    async def execute(self, ctx: ToolContext, image: str) -> Any:
        adapter = ctx.adapter
        if not hasattr(adapter, "ocr_image"):
            return {"error": "当前适配器不支持 OCR 扩展 API"}
        result = _safe_call(
            lambda: adapter.ocr_image(image),
            fallback="当前平台不支持图片 OCR(go-cqhttp/NapCat 扩展)",
        )
        if isinstance(result, dict) and "error" in result:
            return result
        # go-cqhttp 返回 {texts: [{text, confidence, coordinates}]} 或 {texts: "..."}
        texts = result.get("texts", []) if isinstance(result, dict) else result
        if isinstance(texts, str):
            return {"content": texts or "(未识别到文字)"}
        if isinstance(texts, list):
            lines = [t.get("text", "") for t in texts if isinstance(t, dict)]
            return {"content": "\n".join(lines) or "(未识别到文字)", "confidence": texts}
        return {"content": str(result or "(未识别到文字)")}
