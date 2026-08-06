"""QQ 富媒体消息构造器(CQ 码)。

将结构化媒体片段(图片/语音/视频/表情/骰子/音乐/合并转发)拼接为
OneBot v11 可识别的 CQ 码字符串。供 respond 阶段与 qq_social_tools 使用,
让 Agent 输出与 QQ 富媒体直接互通(参照 AstrBot 富媒体输出 + NapCat CQ 支持)。

CQ 码参考:https://doc.napneko.icu/ 与 OneBot v11 规范。
"""
from __future__ import annotations

from typing import Any

from .message import MessageChain, MessageSegment, escape_cq, unescape_cq


def cq_image(file: str | None = None, url: str | None = None, *, cache: bool = True) -> str:
    """图片。file 支持本地路径/URL/base64(data:image/...)。"""
    data: dict[str, Any] = {}
    if file:
        data["file"] = file
    if url:
        data["url"] = url
    if not cache:
        data["cache"] = "0"
    return _cq("image", data)


def cq_record(file: str, *, magic: bool = False) -> str:
    """语音(record)。"""
    return _cq("record", {"file": file, **({"magic": "1"} if magic else {})})


def cq_video(file: str) -> str:
    """短视频。"""
    return _cq("video", {"file": file})


def cq_face(face_id: int) -> str:
    """QQ 表情。"""
    return _cq("face", {"id": str(face_id)})


def cq_dice() -> str:
    """掷骰子(1-6 随机,由客户端展示)。"""
    return _cq("dice", {})


def cq_rps() -> str:
    """猜拳(石头剪刀布)。"""
    return _cq("rps", {})


def cq_music(
    kind: str = "163", music_id: int | str = "", *, title: str = "", url: str = ""
) -> str:
    """音乐分享。kind: 163(网易云) | qq(QQ音乐) | custom(自定义)。
    网易云/QQ音乐传 music_id;custom 传 title+url。非法 kind 抛 ValueError。
    """
    if kind not in ("163", "qq", "custom"):
        raise ValueError(f"未知音乐类型: {kind}(支持 163 / qq / custom)")
    if kind == "custom":
        if not (title and url):
            raise ValueError("自定义音乐需要 title 和 url")
        return _cq("music", {"type": "custom", "title": title, "url": url})
    if not music_id:
        raise ValueError(f"音乐类型 {kind} 需要 music_id")
    return _cq("music", {"type": kind, "id": str(music_id)})


def cq_json(data: dict[str, Any]) -> str:
    """JSON 富媒体(如小程序卡片/转发卡片)。"""
    import json

    return _cq("json", {"data": json.dumps(data, ensure_ascii=False)})


def cq_at(qq: str | int) -> str:
    return _cq("at", {"qq": str(qq)})


def cq_reply(message_id: int) -> str:
    return _cq("reply", {"id": str(message_id)})


def _cq(seg_type: str, data: dict[str, Any]) -> str:
    if not data:
        return f"[CQ:{seg_type}]"
    parts = ",".join(f"{k}={escape_cq(str(v))}" for k, v in data.items())
    return f"[CQ:{seg_type},{parts}]"


def build_rich_message(parts: list[Any]) -> str:
    """把混合内容(字符串/CQ码/MessageSegment/dict)拼成一条完整消息。

    段落类型:
      - str: 若是 CQ 码直接透传,否则作为文本
      - {"type": "image", "file": ...} 等消息段 dict
      - MessageSegment
    """
    out: list[str] = []
    for p in parts:
        if isinstance(p, MessageSegment):
            out.append(p.to_cq_string())
        elif isinstance(p, dict):
            seg_type = p.get("type", "text")
            data = {k: v for k, v in p.items() if k != "type"}
            out.append(_cq(seg_type, data))
        else:
            s = str(p)
            if s.startswith("[CQ:"):
                out.append(s)
            else:
                out.append(escape_cq(unescape_cq(s)))
    return "".join(out)


def chain_to_cq(chain: MessageChain) -> str:
    """MessageChain → CQ 字符串(兼容入口)。"""
    return chain.to_cq_string()
