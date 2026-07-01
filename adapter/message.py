"""消息段与消息链 — 模仿 NoneBot2 的 Message/MessageSegment 设计.

消息链 (MessageChain) 是消息段 (MessageSegment) 的有序列表。
每个消息段有 type 和 data 字段，如:
  MessageSegment(type="text", data={"text": "你好"})
  MessageSegment(type="image", data={"url": "https://..."})
  MessageSegment(type="at", data={"qq": "123456"})
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


@dataclass
class MessageSegment:
    """消息段 — 消息的最小单元."""

    type: str
    """消息段类型: text / image / at / record / video / reply / face / json"""

    data: dict[str, Any] = field(default_factory=dict)
    """消息段数据"""

    # ---- 工厂方法 ----

    @classmethod
    def text(cls, text: str) -> "MessageSegment":
        return cls(type="text", data={"text": text})

    @classmethod
    def image(cls, url: str, file: str = "") -> "MessageSegment":
        return cls(type="image", data={"url": url, "file": file})

    @classmethod
    def at(cls, qq: str) -> "MessageSegment":
        return cls(type="at", data={"qq": qq})

    @classmethod
    def reply(cls, message_id: str) -> "MessageSegment":
        return cls(type="reply", data={"id": message_id})

    @classmethod
    def record(cls, url: str) -> "MessageSegment":
        return cls(type="record", data={"url": url})

    @classmethod
    def video(cls, url: str) -> "MessageSegment":
        return cls(type="video", data={"url": url})

    @classmethod
    def face(cls, face_id: str) -> "MessageSegment":
        return cls(type="face", data={"id": face_id})

    # ---- 序列化 ----

    def to_dict(self) -> dict:
        return {"type": self.type, "data": self.data}

    def to_cq(self) -> str:
        """转换为 CQ 码字符串 (OneBot v11 格式)."""
        if self.type == "text":
            return self.data.get("text", "")
        if self.type == "at":
            return f"[CQ:at,qq={self.data.get('qq', '')}]"
        if self.type == "image":
            url = self.data.get("url", "")
            file = self.data.get("file", "")
            return f"[CQ:image,file={file},url={url}]"
        if self.type == "reply":
            return f"[CQ:reply,id={self.data.get('id', '')}]"
        if self.type == "record":
            return f"[CQ:record,file={self.data.get('url', '')}]"
        if self.type == "video":
            return f"[CQ:video,file={self.data.get('url', '')}]"
        if self.type == "face":
            return f"[CQ:face,id={self.data.get('id', '')}]"
        return ""

    def __str__(self) -> str:
        if self.type == "text":
            return self.data.get("text", "")
        return self.to_cq()


class MessageChain:
    """消息链 — MessageSegment 的有序列表 (参考 NoneBot2 Message)."""

    def __init__(self, segments: list[MessageSegment] | None = None):
        self._segments: list[MessageSegment] = segments or []

    # ---- 构造 ----

    @classmethod
    def from_text(cls, text: str) -> "MessageChain":
        return cls([MessageSegment.text(text)])

    @classmethod
    def from_cq_string(cls, raw: str) -> "MessageChain":
        """从 OneBot v11 的 CQ 码字符串解析为 MessageChain.

        Args:
            raw: 包含 CQ 码的原始消息字符串

        Returns:
            解析后的 MessageChain
        """
        segments = []
        # 匹配 CQ 码 [CQ:type,key=val,...]
        cq_pattern = re.compile(r"\[CQ:(\w+),([^\]]+)\]")

        pos = 0
        for match in cq_pattern.finditer(raw):
            # 前置文本
            if match.start() > pos:
                text = raw[pos : match.start()]
                if text:
                    segments.append(MessageSegment.text(text))

            cq_type = match.group(1)
            cq_data_str = match.group(2)

            # 解析 CQ 码数据
            data: dict[str, str] = {}
            for kv in cq_data_str.split(","):
                if "=" in kv:
                    k, v = kv.split("=", 1)
                    data[k.strip()] = v.strip()

            # 处理特殊类型
            if cq_type == "at":
                data["qq"] = data.get("qq", "")
            elif cq_type == "image":
                data.setdefault("url", data.get("url", ""))
                data.setdefault("file", data.get("file", ""))

            segments.append(MessageSegment(type=cq_type, data=data))
            pos = match.end()

        # 尾部文本
        if pos < len(raw):
            text = raw[pos:]
            if text:
                segments.append(MessageSegment.text(text))

        return cls(segments)

    # ---- 操作 ----

    def add(self, segment: MessageSegment) -> "MessageChain":
        self._segments.append(segment)
        return self

    def add_text(self, text: str) -> "MessageChain":
        return self.add(MessageSegment.text(text))

    def add_at(self, qq: str) -> "MessageChain":
        return self.add(MessageSegment.at(qq))

    def add_image(self, url: str) -> "MessageChain":
        return self.add(MessageSegment.image(url))

    def extract_text(self) -> str:
        """提取所有纯文本消息段."""
        return "".join(seg.data.get("text", "") for seg in self._segments if seg.type == "text")

    def filter(self, seg_type: str) -> list[MessageSegment]:
        """按类型过滤消息段."""
        return [seg for seg in self._segments if seg.type == seg_type]

    def to_cq_string(self) -> str:
        """转换为 OneBot v11 的 CQ 码字符串."""
        return "".join(seg.to_cq() for seg in self._segments)

    # ---- 容器协议 ----

    def __iter__(self):
        return iter(self._segments)

    def __getitem__(self, index):
        return self._segments[index]

    def __len__(self) -> int:
        return len(self._segments)

    def __bool__(self) -> bool:
        return len(self._segments) > 0

    def __repr__(self) -> str:
        return f"<MessageChain segments={len(self._segments)}>"
