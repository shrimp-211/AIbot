"""Star(插件)元数据与注册表(compat)。"""

from __future__ import annotations

from typing import Any

star_registry: dict = {}


class StarMetadata:
    """插件元数据(映射本项目 plugins/metadata_schema 的字段)。"""

    def __init__(
        self,
        name: str = "",
        description: str = "",
        version: str = "1.0.0",
        author: str = "",
        **kwargs: Any,
    ) -> None:
        self.name = name
        self.description = description
        self.version = version
        self.author = author
        self.extra = kwargs

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "author": self.author,
            **self.extra,
        }

    def __repr__(self) -> str:  # pragma: no cover
        return f"<StarMetadata name={self.name} version={self.version}>"
