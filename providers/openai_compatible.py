"""OpenAI 兼容 Provider(覆盖 OpenAI / DeepSeek / Ollama / SiliconFlow 等)。

使用 openai 官方 SDK,设置 base_url 即可接入任意 OpenAI 兼容服务。
"""
from __future__ import annotations

import json
from typing import Any

from openai import AsyncOpenAI
from loguru import logger

from .base import BaseProvider


class OpenAICompatibleProvider(BaseProvider):
    name = "openai_compatible"

    def __init__(self, config: dict[str, Any]):
        super().__init__(config)
        # reasoning_effort(OpenAI o1/o3 系列):配置时替代 temperature
        self.reasoning_effort = config.get("reasoning_effort")
        self._client = AsyncOpenAI(
            api_key=self.api_key or "sk-not-set",
            base_url=self.base_url or None,
            timeout=60.0,
        )

    async def chat(
        self,
        messages: list[dict],
        system_prompt: str | None = None,
        tools: list[dict] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        msgs: list[dict] = []
        if system_prompt:
            msgs.append({"role": "system", "content": system_prompt})
        msgs.extend(messages)

        params: dict[str, Any] = dict(
            model=self.model,
            messages=msgs,
            max_tokens=kwargs.get("max_tokens", self.max_tokens),
        )
        if self.reasoning_effort:
            # 推理模型(o1/o3 系)不接受 temperature,用 reasoning_effort
            params["reasoning_effort"] = kwargs.get(
                "reasoning_effort", self.reasoning_effort
            )
        else:
            params["temperature"] = kwargs.get("temperature", self.temperature)
        if tools:
            params["tools"] = [{"type": "function", "function": t} for t in tools]

        resp = await self._client.chat.completions.create(**params)
        choice = resp.choices[0]
        msg = choice.message

        content = msg.content or ""
        tool_calls = []
        if msg.tool_calls:
            for tc in msg.tool_calls:
                try:
                    arguments = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    arguments = {}
                tool_calls.append(
                    {"id": tc.id, "name": tc.function.name, "arguments": arguments}
                )
        return {"content": content, "tool_calls": tool_calls, "raw": msg}

    async def test(self) -> bool:
        try:
            await self._client.models.list()
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"Provider 连通性测试失败: {exc}")
            return False
