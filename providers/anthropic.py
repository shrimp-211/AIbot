"""Anthropic Claude Provider.

支持 Anthropic Claude 模型 (Claude Opus 4, Sonnet 4.6, Haiku 4.5).
"""

import json
from typing import Any

import httpx

from .base import BaseProvider


class AnthropicProvider(BaseProvider):
    """Anthropic Claude Provider — 预转换工具格式."""

    def __init__(self, model: str, api_key: str, base_url: str, **kwargs):
        super().__init__(model, api_key, base_url, **kwargs)
        self._tool_cache: dict[int, list[dict]] = {}  # hash(tool_names) → anthropic_tools

    @staticmethod
    def _convert_tools(tools: list[dict]) -> list[dict]:
        return [{
            "name": t["function"]["name"],
            "description": t["function"]["description"],
            "input_schema": t["function"]["parameters"],
        } for t in tools]

    def _get_anthropic_tools(self, tools: list[dict] | None) -> list[dict] | None:
        if not tools:
            return None
        # 用工具名列表的 hash 做缓存键
        key = hash(tuple(sorted(t["function"]["name"] for t in tools)))
        if key not in self._tool_cache:
            self._tool_cache[key] = self._convert_tools(tools)
        return self._tool_cache[key]

    async def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        **kwargs,
    ) -> dict[str, Any]:
        """调用 Anthropic Messages API."""
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }

        base = self.base_url.rstrip("/")

        # 分离 system 消息
        system_content = None
        user_messages = []
        for msg in messages:
            if msg["role"] == "system":
                system_content = msg["content"]
            else:
                user_messages.append(msg)

        payload = {
            "model": self.model,
            "messages": user_messages,
            "max_tokens": self.extra.get("max_tokens", 4096),
        }

        if system_content:
            payload["system"] = system_content

        # OpenAI → Anthropic tools (预转换+缓存)
        anthropic_tools = self._get_anthropic_tools(tools)
        if anthropic_tools:
            payload["tools"] = anthropic_tools

        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{base}/v1/messages",
                headers=headers,
                json=payload,
            )

            if resp.status_code != 200:
                error_text = resp.text[:500]
                raise RuntimeError(f"Claude API 错误 ({resp.status_code}): {error_text}")

            data = resp.json()

            result = {
                "content": "",
                "tool_calls": None,
            }

            # 解析响应内容
            for block in data.get("content", []):
                if block["type"] == "text":
                    result["content"] += block["text"]
                elif block["type"] == "tool_use":
                    # Anthropic tool_use → OpenAI tool_call 格式
                    if result["tool_calls"] is None:
                        result["tool_calls"] = []
                    result["tool_calls"].append({
                        "id": block["id"],
                        "type": "function",
                        "function": {
                            "name": block["name"],
                            "arguments": json.dumps(block["input"]),
                        },
                    })

            return result
