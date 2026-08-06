"""Anthropic Provider(Claude 系列模型)。

支持 Anthropic 的 tool use 协议,内部把 OpenAI 格式的工具定义
转换为 Anthropic 的 input_schema 格式。
"""
from __future__ import annotations

import json
from typing import Any

from loguru import logger

from .base import BaseProvider


class AnthropicProvider(BaseProvider):
    name = "anthropic"

    def __init__(self, config: dict[str, Any]):
        super().__init__(config)
        self.api_version = config.get("api_version", "2023-06-01")
        self.max_tokens = int(config.get("max_tokens", 8192) or 8192)
        # extended_thinking(参考 Claude Code):thinking.enabled + budget_tokens
        thinking_cfg = config.get("thinking") or {}
        if isinstance(thinking_cfg, bool):
            thinking_cfg = {"enabled": thinking_cfg}
        self.thinking_enabled = bool(
            thinking_cfg.get("enabled", config.get("thinking_enabled", False))
        )
        self.thinking_budget = int(
            thinking_cfg.get("budget_tokens", config.get("thinking_budget_tokens", 2048))
            or 2048
        )
        self._client = None  # lazy init

    def _get_client(self):
        if self._client is None:
            try:
                import anthropic
            except ImportError as exc:  # pragma: no cover
                raise RuntimeError("未安装 anthropic 库: pip install anthropic") from exc
            kwargs: dict[str, Any] = {
                "api_key": self.api_key,
                "timeout": 60.0,
            }
            if self.base_url:
                kwargs["base_url"] = self.base_url
            self._client = anthropic.AsyncAnthropic(**kwargs)
        return self._client

    @staticmethod
    def _convert_tool(tool: dict) -> dict:
        return {
            "name": tool["name"],
            "description": tool.get("description", ""),
            "input_schema": tool.get("parameters") or {"type": "object", "properties": {}},
        }

    async def chat(
        self,
        messages: list[dict],
        system_prompt: str | None = None,
        tools: list[dict] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        client = self._get_client()
        anthropic_messages: list[dict] = []
        # 把 OpenAI 格式消息转换为 Anthropic 格式
        for m in messages:
            role = m["role"]
            content = m.get("content", "")
            if role == "tool":
                anthropic_messages.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": m.get("tool_call_id", ""),
                                "content": str(content),
                            }
                        ],
                    }
                )
            elif role == "assistant" and m.get("tool_calls"):
                blocks = []
                if content:
                    blocks.append({"type": "text", "text": content})
                for tc in m["tool_calls"]:
                    blocks.append(
                        {
                            "type": "tool_use",
                            "id": tc.get("id", ""),
                            "name": tc.get("name", ""),
                            "input": tc.get("arguments", {}),
                        }
                    )
                anthropic_messages.append({"role": "assistant", "content": blocks})
            else:
                anthropic_messages.append({"role": role, "content": content})

        params: dict[str, Any] = dict(
            model=self.model,
            messages=anthropic_messages,
            max_tokens=kwargs.get("max_tokens", self.max_tokens),
        )
        if self.thinking_enabled:
            # extended thinking 与 temperature 互斥:启用时不传 temperature
            params["thinking"] = {
                "type": "enabled",
                "budget_tokens": min(
                    self.thinking_budget, params["max_tokens"] - 1
                ),
            }
        else:
            params["temperature"] = kwargs.get("temperature", self.temperature)
        if system_prompt:
            params["system"] = system_prompt
        if tools:
            params["tools"] = [self._convert_tool(t) for t in tools]

        resp = await self._with_retry(lambda: client.messages.create(**params))

        content_parts: list[str] = []
        tool_calls = []
        thinking_text: list[str] = []
        for block in resp.content:
            if block.type == "text":
                content_parts.append(block.text)
            elif block.type == "thinking":
                # 推理过程不进入最终回复,存入 raw 供上层使用
                thinking_text.append(block.thinking)
            elif block.type == "tool_use":
                tool_calls.append(
                    {
                        "id": block.id,
                        "name": block.name,
                        "arguments": block.input or {},
                    }
                )
        return {
            "content": "".join(content_parts),
            "tool_calls": tool_calls,
            "raw": resp,
            "thinking": "\n".join(thinking_text),
        }

    async def test(self) -> bool:
        try:
            await self._get_client().messages.create(
                model=self.model,
                max_tokens=8,
                messages=[{"role": "user", "content": "hi"}],
            )
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"Anthropic 连通性测试失败: {exc}")
            return False
