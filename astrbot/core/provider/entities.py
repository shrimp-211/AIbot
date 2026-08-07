"""Provider 元数据实体(AstrBot 兼容,精简)。"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any


class ProviderType(enum.Enum):
    CHAT_COMPLETION = "chat_completion"
    SPEECH_TO_TEXT = "speech_to_text"
    TEXT_TO_SPEECH = "text_to_speech"
    EMBEDDING = "embedding"
    RERANK = "rerank"


@dataclass
class ProviderMeta:
    """The basic metadata of a provider instance."""

    id: str
    model: str | None
    type: str
    provider_type: ProviderType = ProviderType.CHAT_COMPLETION


@dataclass
class ProviderMetaData(ProviderMeta):
    """The metadata of a provider adapter for registration."""

    desc: str = ""
    cls_type: Any = None
    default_config_tmpl: dict | None = None
    config_metadata: dict | None = field(default_factory=dict)
