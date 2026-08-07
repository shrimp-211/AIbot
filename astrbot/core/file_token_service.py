"""文件令牌服务:为 dashboard 的临时文件链接签发短期访问令牌(AstrBot 兼容)。"""

from __future__ import annotations

import time
import uuid

from astrbot.core import logger


class FileTokenService:
    def __init__(self, default_timeout: float = 300) -> None:
        self.default_timeout = default_timeout
        self._tokens: dict[str, dict] = {}

    async def _cleanup_expired_tokens(self) -> None:
        now = time.time()
        expired = [k for k, v in self._tokens.items() if v["expires_at"] < now]
        for k in expired:
            self._tokens.pop(k, None)

    async def check_token_expired(self, file_token: str) -> bool:
        """返回 True 表示 token 已失效/不存在。"""
        await self._cleanup_expired_tokens()
        return file_token not in self._tokens

    async def register_file(
        self, file_path: str, timeout: float | None = None
    ) -> str:
        await self._cleanup_expired_tokens()
        token = uuid.uuid4().hex
        self._tokens[token] = {
            "path": file_path,
            "expires_at": time.time() + (timeout or self.default_timeout),
        }
        return token

    async def handle_file(self, file_token: str) -> str:
        """解析 token 返回文件路径;无效则抛 KeyError。"""
        await self._cleanup_expired_tokens()
        if file_token not in self._tokens:
            raise KeyError("文件令牌无效或已过期")
        return self._tokens[file_token]["path"]
