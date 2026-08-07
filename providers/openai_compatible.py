"""OpenAI 兼容 Provider(覆盖 OpenAI / DeepSeek / Ollama / SiliconFlow 等)。

使用 openai 官方 SDK,设置 base_url 即可接入任意 OpenAI 兼容服务。
"""
from __future__ import annotations

import json
from typing import Any

from openai import AsyncOpenAI
from loguru import logger

from .base import BaseProvider
from .modalities import (
    DEFAULT_MODALITIES,
    MODALITY_FUNCTION_CALL,
    MODALITY_IMAGE,
    MODALITY_STREAMING,
    MODALITY_TEXT,
)


class OpenAICompatibleProvider(BaseProvider):
    name = "openai_compatible"

    def __init__(self, config: dict[str, Any]):
        super().__init__(config)
        # reasoning_effort(OpenAI o1/o3 系列):配置时替代 temperature
        self.reasoning_effort = config.get("reasoning_effort")
        if self.reasoning_effort not in (None, "low", "medium", "high"):
            logger.warning("忽略非法的 reasoning_effort: {}", self.reasoning_effort)
            self.reasoning_effort = None
        self._client = AsyncOpenAI(
            api_key=self.api_key or "sk-not-set",
            base_url=self.base_url or None,
            timeout=60.0,
        )
        # 多模态模型(gpt-4o 及更新)额外声明图像能力
        model = (self.model or "").lower()
        if any(k in model for k in ("gpt-4o", "gpt-4.1", "gpt-4.5", "vision", "vl", "qwen2.5-vl")):
            self.modalities = DEFAULT_MODALITIES | {MODALITY_IMAGE}
        if model and not self.reasoning_effort:
            self.modalities |= {MODALITY_STREAMING, MODALITY_FUNCTION_CALL}

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

        resp = await self._with_retry(lambda: self._client.chat.completions.create(**params))
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
        # token 用量在完整 response 上(choice.message 无 usage),供上层成本统计
        usage = getattr(resp, "usage", None)
        return {
            "content": content,
            "tool_calls": tool_calls,
            "raw": msg,
            "usage": {
                "prompt_tokens": int(getattr(usage, "prompt_tokens", 0) or 0),
                "completion_tokens": int(getattr(usage, "completion_tokens", 0) or 0),
            }
            if usage is not None
            else {},
        }

    async def chat_stream(
        self,
        messages: list[dict],
        system_prompt: str | None = None,
        tools: list[dict] | None = None,
        **kwargs: Any,
    ):
        """OpenAI 兼容真流式:逐 delta 产出 content/reasoning,工具调用增量聚合。"""
        msgs: list[dict] = []
        if system_prompt:
            msgs.append({"role": "system", "content": system_prompt})
        msgs.extend(messages)
        params: dict[str, Any] = dict(
            model=self.model,
            messages=msgs,
            max_tokens=kwargs.get("max_tokens", self.max_tokens),
            stream=True,
        )
        if self.reasoning_effort:
            params["reasoning_effort"] = kwargs.get("reasoning_effort", self.reasoning_effort)
        else:
            params["temperature"] = kwargs.get("temperature", self.temperature)
        if tools:
            params["tools"] = [{"type": "function", "function": t} for t in tools]

        stream = await self._with_retry(
            lambda: self._client.chat.completions.create(**params)
        )
        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        tool_calls: dict[int, dict[str, str]] = {}
        usage = None
        async for chunk in stream:
            if getattr(chunk, "usage", None):
                usage = chunk.usage
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if delta is None:
                continue
            content = getattr(delta, "content", None)
            if content:
                content_parts.append(content)
                yield {"type": "delta", "content": content, "reasoning": ""}
            reasoning = getattr(delta, "reasoning_content", None)
            if reasoning:
                reasoning_parts.append(reasoning)
                yield {"type": "delta", "content": "", "reasoning": reasoning}
            if getattr(delta, "tool_calls", None):
                for tc in delta.tool_calls:
                    idx = tc.index
                    entry = tool_calls.setdefault(idx, {"id": "", "name": "", "arguments": ""})
                    if tc.id:
                        entry["id"] = tc.id
                    if tc.function:
                        if tc.function.name:
                            entry["name"] += tc.function.name
                        if tc.function.arguments:
                            entry["arguments"] += tc.function.arguments

        result: dict[str, Any] = {
            "content": "".join(content_parts),
            "tool_calls": [],
            "thinking": "\n".join(reasoning_parts),
        }
        if tool_calls:
            tcs = []
            for idx in sorted(tool_calls):
                entry = tool_calls[idx]
                try:
                    arguments = json.loads(entry["arguments"] or "{}")
                except json.JSONDecodeError:
                    arguments = {}
                tcs.append({"id": entry["id"], "name": entry["name"], "arguments": arguments})
            result["tool_calls"] = tcs
        if usage is not None:
            result["usage"] = {
                "prompt_tokens": int(getattr(usage, "prompt_tokens", 0) or 0),
                "completion_tokens": int(getattr(usage, "completion_tokens", 0) or 0),
            }
        yield {"type": "done", **result}

    async def test(self) -> bool:
        try:
            await self._client.models.list()
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"Provider 连通性测试失败: {exc}")
            return False
