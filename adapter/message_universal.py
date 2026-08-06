"""跨平台通用消息模型 UniMessage。

参考 NoneBot2 Alconna 的 Universal Message 设计,抽象出与平台无关的
消息段(Text/Image/At/Reply/Voice/Video/File/Markdown/Face)。平台适配器
负责与平台特定格式互转;不支持的段统一降级为文本描述,保证任何平台
都能安全收发纯文本。

当前提供:
- UniSegment / UniMessage 数据结构
- MessageChain(OneBot) → UniMessage 转换
- 降级文本渲染(to_text)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterator

from .message import MessageChain

_SUPPORTED = ("text", "image", "at", "reply", "voice", "video", "file", "markdown", "face")


@dataclass
class UniSegment:
    type: str
    data: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def text(cls, content: str) -> "UniSegment":
        return cls("text", {"text": content})

    @classmethod
    def image(cls, url: str | None = None, file: str | None = None) -> "UniSegment":
        data: dict[str, Any] = {}
        if url:
            data["url"] = url
        if file:
            data["file"] = file
        return cls("image", data)

    @classmethod
    def at(cls, qq: str | int) -> "UniSegment":
        return cls("at", {"qq": str(qq)})

    @classmethod
    def reply(cls, message_id: str | int) -> "UniSegment":
        return cls("reply", {"message_id": str(message_id)})

    @classmethod
    def voice(cls, url: str | None = None, file: str | None = None) -> "UniSegment":
        data: dict[str, Any] = {}
        if url:
            data["url"] = url
        if file:
            data["file"] = file
        return cls("voice", data)

    @classmethod
    def video(cls, url: str | None = None, file: str | None = None) -> "UniSegment":
        data: dict[str, Any] = {}
        if url:
            data["url"] = url
        if file:
            data["file"] = file
        return cls("video", data)

    @classmethod
    def file(cls, name: str, url: str | None = None, file: str | None = None) -> "UniSegment":
        data: dict[str, Any] = {"name": name}
        if url:
            data["url"] = url
        if file:
            data["file"] = file
        return cls("file", data)

    @classmethod
    def markdown(cls, content: str) -> "UniSegment":
        return cls("markdown", {"content": content})

    @classmethod
    def face(cls, face_id: int) -> "UniSegment":
        return cls("face", {"id": str(face_id)})

    def to_text(self) -> str:
        """降级渲染:非文本段转换为描述文本(保证纯文本通道可用)。"""
        if self.type == "text":
            return self.data.get("text", "")
        if self.type == "at":
            return f"@{self.data.get('qq', '')} "
        if self.type == "reply":
            return ""
        if self.type == "image":
            url = self.data.get("url") or self.data.get("file") or ""
            return f"[图片]{url}"
        if self.type == "voice":
            return "[语音]"
        if self.type == "video":
            return "[视频]"
        if self.type == "file":
            return f"[文件]{self.data.get('name', '')}"
        if self.type == "markdown":
            return self.data.get("content", "")
        if self.type == "face":
            return f"[表情{self.data.get('id', '')}]"
        return f"[{self.type}]"

    def to_markdown(self) -> str:
        """富文本渲染:image/file 尽量输出 url 链接。"""
        if self.type == "image":
            return f"![图片]({self.data.get('url', '')})" if self.data.get("url") else "[图片]"
        if self.type == "file":
            url = self.data.get("url", "")
            name = self.data.get("name", "文件")
            return f"[{name}]({url})" if url else f"[文件]{name}"
        return self.to_text()


class UniMessage(list):
    """通用消息段列表。"""

    @classmethod
    def from_text(cls, text: str) -> "UniMessage":
        return cls([UniSegment.text(text)])

    @classmethod
    def from_message_chain(cls, chain: MessageChain) -> "UniMessage":
        msg = cls()
        for seg in chain:
            t = seg.type
            d = seg.data
            if t in _SUPPORTED:
                msg.append(UniSegment(t, dict(d)))
            elif t == "at":
                msg.append(UniSegment.at(d.get("qq", "")))
            else:
                # 未知段降级为文本
                msg.append(UniSegment.text(seg.to_cq_string() if hasattr(seg, "to_cq_string") else f"[{t}]"))
        return msg

    def extract_plain_text(self) -> str:
        return "".join(s.data.get("text", "") for s in self if s.type == "text")

    def to_text(self) -> str:
        """全部段降级为文本。"""
        return "".join(s.to_text() for s in self)

    def to_markdown(self) -> str:
        return "".join(s.to_markdown() for s in self)

    def get(self, type_: str) -> list[UniSegment]:
        return [s for s in self if s.type == type_]

    def __iter__(self) -> Iterator[UniSegment]:  # type: ignore[override]
        return super().__iter__()
