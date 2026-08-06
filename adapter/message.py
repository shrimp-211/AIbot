"""消息模型:MessageSegment + MessageChain,CQ 码解析与生成。

参考 NoneBot2 的 Message 设计,针对 OneBot v11 CQ 码格式。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterator

_CQ_PATTERN = re.compile(r"\[CQ:([a-zA-Z0-9_]+)((?:,[^\[\]]*?)*)\]")

_ESCAPE_MAP = {"&": "&amp;", "[": "&#91;", "]": "&#93;"}


def escape_cq(text: str) -> str:
    for k, v in _ESCAPE_MAP.items():
        text = text.replace(k, v)
    return text


def unescape_cq(text: str) -> str:
    for v, k in _ESCAPE_MAP.items():
        text = text.replace(v, k)
    return text


@dataclass
class MessageSegment:
    type: str
    data: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def text(cls, content: str) -> "MessageSegment":
        return cls("text", {"text": content})

    @classmethod
    def image(cls, file: str | None = None, url: str | None = None) -> "MessageSegment":
        data: dict[str, Any] = {}
        if file:
            data["file"] = file
        if url:
            data["url"] = url
        return cls("image", data)

    @classmethod
    def at(cls, qq: str | int) -> "MessageSegment":
        return cls("at", {"qq": str(qq)})

    @classmethod
    def reply(cls, message_id: int) -> "MessageSegment":
        return cls("reply", {"id": str(message_id)})

    @classmethod
    def record(cls, file: str) -> "MessageSegment":
        return cls("record", {"file": file})

    @classmethod
    def video(cls, file: str) -> "MessageSegment":
        return cls("video", {"file": file})

    @classmethod
    def face(cls, face_id: int) -> "MessageSegment":
        return cls("face", {"id": str(face_id)})

    @classmethod
    def from_cq(cls, raw: str) -> "MessageSegment":
        """解析单个 CQ 码片段(不含外层 [] 也可处理)。"""
        match = _CQ_PATTERN.match(raw.strip())
        if not match:
            return cls("text", {"text": raw})
        seg_type, params = match.group(1), match.group(2)
        data: dict[str, Any] = {}
        if params:
            for pair in params.split(",")[1:]:
                if "=" in pair:
                    key, _, value = pair.partition("=")
                    data[key.strip()] = unescape_cq(value.strip())
        return cls(seg_type, data)

    def to_cq_string(self) -> str:
        if self.type == "text":
            return escape_cq(self.data.get("text", ""))
        parts = [f"{k}={escape_cq(str(v))}" for k, v in self.data.items()]
        return f"[CQ:{self.type},{','.join(parts)}]"

    def is_text(self) -> bool:
        return self.type == "text"

    def __str__(self) -> str:
        return self.to_cq_string()


class MessageChain(list):
    """消息段列表,支持 CQ 码解析/生成和纯文本提取。"""

    @classmethod
    def from_cq_string(cls, cq: str) -> "MessageChain":
        chain = cls()
        pos = 0
        for match in _CQ_PATTERN.finditer(cq):
            if match.start() > pos:
                chain.append(MessageSegment.text(unescape_cq(cq[pos : match.start()])))
            chain.append(MessageSegment.from_cq(match.group(0)))
            pos = match.end()
        if pos < len(cq):
            chain.append(MessageSegment.text(unescape_cq(cq[pos:])))
        return chain

    @classmethod
    def from_segments(cls, segments: list[dict]) -> "MessageChain":
        """从 OneBot v11 的 message array 构建。"""
        chain = cls()
        for seg in segments:
            chain.append(MessageSegment(seg.get("type", "text"), dict(seg.get("data", {}) or {})))
        return chain

    def extract_plain_text(self) -> str:
        return "".join(s.data.get("text", "") for s in self if s.is_text())

    def to_cq_string(self) -> str:
        return "".join(s.to_cq_string() for s in self)

    def get(self, type_: str) -> list[MessageSegment]:
        return [s for s in self if s.type == type_]

    def __str__(self) -> str:
        return self.to_cq_string()

    def __iter__(self) -> Iterator[MessageSegment]:  # type: ignore[override]
        return super().__iter__()
