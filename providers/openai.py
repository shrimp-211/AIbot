"""OpenAI / OpenAI 兼容 Provider.

支持 OpenAI API 及所有兼容 OpenAI 格式的服务:
- OpenAI (GPT-4o, GPT-4, etc.)
- DeepSeek
- Ollama
- SiliconFlow
- 等各种兼容服务
"""

import json
from typing import Any

import httpx

from .base import BaseProvider


class OpenAIProvider(BaseProvider):
    """OpenAI 兼容 Provider."""

    async def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        **kwargs,
    ) -> dict[str, Any]:
        """调用 OpenAI Chat Completions API."""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        # 处理 base_url 末尾的 /v1
        base = self.base_url.rstrip("/")
        if not base.endswith("/v1"):
            base += "/v1"

        payload = {
            "model": self.model,
            "messages": messages,
            "max_tokens": self.extra.get("max_tokens", 4096),
            "temperature": self.extra.get("temperature", 0.7),
        }

        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{base}/chat/completions",
                headers=headers,
                json=payload,
            )

            if resp.status_code != 200:
                error_text = resp.text[:500]
                raise RuntimeError(f"API 错误 ({resp.status_code}): {error_text}")

            data = resp.json()
            choice = data["choices"][0]
            message = choice["message"]

            result = {
                "content": message.get("content"),
                "tool_calls": None,
                "usage": data.get("usage", {}),
            }

            # 处理工具调用
            if message.get("tool_calls"):
                result["tool_calls"] = message["tool_calls"]

            return result
