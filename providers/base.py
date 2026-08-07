"""LLM Provider 抽象 + 工厂函数。

统一 `chat()` 接口,返回 `{"content": str, "tool_calls": [...]}`。
tool_calls 元素格式:
    {"id": str, "name": str, "arguments": dict}
"""
from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from typing import Any

from loguru import logger

from .modalities import DEFAULT_MODALITIES

# 模型 -> 上下文窗口(按模型名子串匹配,越长 key 越优先)
_CONTEXT_WINDOWS: dict[str, int] = {
    "opus-4": 1_000_000,
    "sonnet-4": 1_000_000,
    "haiku-4": 200_000,
    "claude-3": 200_000,
    "claude": 200_000,
    "deepseek-r1": 163_840,
    "deepseek": 163_840,
    "gpt-4.1": 1_000_000,
    "gpt-4o": 128_000,
    "gpt-4": 128_000,
    "o1": 200_000,
    "o3": 200_000,
    "glm": 128_000,
    "qwen": 131_072,
    "kimi": 131_072,
    "moonshot": 131_072,
    "gemini": 1_000_000,
    "llama": 128_000,
    "mistral": 128_000,
}

_DEFAULT_CONTEXT_WINDOW = 128_000


def estimate_context_window(model: str) -> int:
    """按模型名推断上下文窗口(越具体的 key 越优先)。"""
    m = (model or "").lower()
    for key, win in sorted(_CONTEXT_WINDOWS.items(), key=lambda kv: -len(kv[0])):
        if key in m:
            return win
    return _DEFAULT_CONTEXT_WINDOW


class BaseProvider(ABC):
    name: str = "base"
    # 模态能力声明:默认文本 + 函数调用;多模态模型在子类覆盖
    modalities: frozenset[str] = DEFAULT_MODALITIES
    # 图片 content block 协议格式:openai(image_url) | anthropic(base64 source)。
    # 供上层视觉分析回调构建消息块;fallback 链(ProviderManager)经 __getattr__ 委托到活动 provider。
    image_block_format: str = "openai"

    def __init__(self, config: dict[str, Any]):
        self.config = config
        self.model = config.get("model", "")
        self.base_url = config.get("base_url", "")
        self.api_key = config.get("api_key", "")
        self.max_tokens = int(config.get("max_tokens", 4096) or 4096)
        self.temperature = float(config.get("temperature", 0.7) or 0.7)
        # 上下文窗口:配置显式覆盖优先,否则按模型名推断
        configured = int(config.get("context_window", 0) or 0)
        self.context_window = configured or estimate_context_window(self.model)
        # 指数退避重试(网络抖动/限流/5xx 时自动重试)
        retry_cfg = config.get("retry") or {}
        self.max_attempts = max(
            1, int(retry_cfg.get("max_attempts", config.get("max_retries", 3)) or 3)
        )
        self.retry_base_delay = float(retry_cfg.get("base_delay", 0.5) or 0.5)
        self.retry_max_delay = float(retry_cfg.get("max_delay", 8.0) or 8.0)

    # ---------- 重试 ----------

    _RETRYABLE_CODES = frozenset((408, 429, 500, 502, 503, 504))

    @classmethod
    def _is_retryable(cls, exc: Exception) -> bool:
        """判断异常是否可重试:限流/超时/5xx/传输层错误。"""
        name = type(exc).__name__
        if name in (
            "RateLimitError",
            "APITimeoutError",
            "InternalServerError",
            "BadGatewayError",
            "ServiceUnavailableError",
            "APIConnectionError",
            "ConnectTimeout",
            "ReadTimeout",
            "TimeoutError",
        ):
            return True
        code = getattr(exc, "status_code", None)
        if code is None:
            # httpx 等库把状态码放在 exc.response.status_code(HTTPStatusError)
            response = getattr(exc, "response", None)
            code = getattr(response, "status_code", None)
        if code is not None:
            # 仅限流(429)与 5xx 重试,4xx 永久性错误不重试
            return code in cls._RETRYABLE_CODES
        # 传输层库异常兜底(httpx / aiohttp)
        mod = type(exc).__module__ or ""
        return "httpx" in mod or "aiohttp" in mod

    async def _with_retry(self, fn):
        """指数退避重试包装:base_delay * 2^i 递增,封顶 max_delay。"""
        last_exc: Exception | None = None
        for i in range(self.max_attempts):
            try:
                return await fn()
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                if not self._is_retryable(exc) or i == self.max_attempts - 1:
                    raise
                delay = min(self.retry_base_delay * (2**i), self.retry_max_delay)
                logger.warning(
                    "{} 调用失败({}),{:.1f}s 后重试({}/{})",
                    self.name,
                    type(exc).__name__,
                    delay,
                    i + 1,
                    self.max_attempts,
                )
                await asyncio.sleep(delay)
        assert last_exc is not None
        raise last_exc

    def set_model(self, model: str) -> None:
        """运行时切换模型(热切换, 供 /model 命令),同步更新上下文窗口推断。"""
        self.model = model or self.model
        self.config["model"] = self.model
        self.context_window = estimate_context_window(self.model)

    @abstractmethod
    async def chat(
        self,
        messages: list[dict],
        system_prompt: str | None = None,
        tools: list[dict] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """单次对话。

        Args:
            messages: OpenAI 格式消息列表
            system_prompt: 可选的 system prompt
            tools: OpenAI 格式工具定义列表(含 name/description/parameters)
        Returns:
            {"content": str, "tool_calls": [...], "raw": Any}
        """
        raise NotImplementedError

    @abstractmethod
    async def test(self) -> bool:
        """连通性测试。"""
        raise NotImplementedError

    async def chat_stream(
        self,
        messages: list[dict],
        system_prompt: str | None = None,
        tools: list[dict] | None = None,
        **kwargs: Any,
    ):
        """流式调用,产出 delta 块直至 done 块。

        delta 块: {"type":"delta","content":str,"reasoning":str}
        done 块:  {"type":"done","content","tool_calls","thinking","usage",...}(与 chat() 结构一致)

        默认实现回退为一次性 chat()(无真流式能力的 provider 仍可被上层以统一方式消费)。
        """
        result = await self.chat(messages, system_prompt=system_prompt, tools=tools, **kwargs)
        content = result.get("content") or ""
        reasoning = result.get("thinking") or ""
        if content or reasoning:
            yield {"type": "delta", "content": content, "reasoning": reasoning}
        yield {"type": "done", **result}


def create_provider(config: dict[str, Any]) -> BaseProvider:
    """按 type 创建 provider 实例。

    配置含 `fallback_providers` 时返回 ProviderManager(失败自动切换备用)。
    """
    if config.get("fallback_providers"):
        from .manager import ProviderManager

        return ProviderManager(config)
    ptype = (config.get("type") or "").lower()
    if ptype in ("anthropic", "claude"):
        from .anthropic import AnthropicProvider

        return AnthropicProvider(config)
    from .openai_compatible import OpenAICompatibleProvider

    return OpenAICompatibleProvider(config)


# ---------------------------------------------------------------------------
# 多模态 Provider 抽象(参照 AstrBot: Chat/STT/TTS/Embedding/Rerank 五类)
# 前四类在此定义,与 LLM 并列;实际实现放在 providers/sources/ 下。
# ---------------------------------------------------------------------------


class STTProvider(ABC):
    """语音转文字(识别)。"""
    name: str = "stt"

    def __init__(self, config: dict[str, Any]):
        self.config = config or {}

    @abstractmethod
    async def transcribe(self, audio_path: str, **kwargs: Any) -> str:
        """将本地音频文件转写为文本。"""
        raise NotImplementedError

    async def test(self) -> bool:
        return bool(self.config.get("api_key"))


class TTSProvider(ABC):
    """文字转语音(合成)。"""
    name: str = "tts"

    def __init__(self, config: dict[str, Any]):
        self.config = config or {}

    @abstractmethod
    async def synthesize(self, text: str, output_path: str, **kwargs: Any) -> str:
        """将文本合成为语音文件,返回文件路径。"""
        raise NotImplementedError

    async def test(self) -> bool:
        return True


class EmbeddingProvider(ABC):
    """文本向量化。"""
    name: str = "embedding"

    def __init__(self, config: dict[str, Any]):
        self.config = config or {}
        self.model = (config or {}).get("model", "")

    @abstractmethod
    async def embed(self, texts: list[str], **kwargs: Any) -> list[list[float]]:
        """将文本列表编码为向量列表。"""
        raise NotImplementedError

    async def test(self) -> bool:
        return True


class RerankProvider(ABC):
    """检索结果重排序。"""
    name: str = "rerank"

    def __init__(self, config: dict[str, Any]):
        self.config = config or {}
        self.model = (config or {}).get("model", "")

    @abstractmethod
    async def rerank(self, query: str, documents: list[str], **kwargs: Any) -> list[float]:
        """按与 query 的相关度给 documents 打分(0~1,越大越相关)。"""
        raise NotImplementedError

    async def test(self) -> bool:
        return bool(self.config.get("api_key"))


def create_stt_provider(config: dict[str, Any]) -> STTProvider | None:
    ptype = (config or {}).get("type", "whisper")
    if ptype in ("none", "disabled"):
        return None
    if ptype in ("whisper", "openai"):
        from .sources.stt_whisper import WhisperSTTProvider

        return WhisperSTTProvider(config or {})
    raise ValueError(f"未知 STT Provider: {ptype}")


def create_tts_provider(config: dict[str, Any]) -> TTSProvider | None:
    ptype = (config or {}).get("type", "edge")
    if ptype in ("none", "disabled"):
        return None
    if ptype in ("edge", "edge-tts"):
        from .sources.tts_edge import EdgeTTSProvider

        return EdgeTTSProvider(config or {})
    raise ValueError(f"未知 TTS Provider: {ptype}")


class ChainedEmbeddingProvider(EmbeddingProvider):
    """多后端 Embedding 链:按序尝试,前一个失败自动切换到下一个。

    适配度保障:openai 无 Key/网络失败时自动回退本地 sentence-transformers,
    保证向量能力在任意环境下可用。
    """

    name = "chained"

    def __init__(self, providers: list[EmbeddingProvider]):
        super().__init__({})
        self.providers = [p for p in providers if p is not None]

    async def embed(self, texts: list[str], **kwargs: Any) -> list[list[float]]:
        last_exc: Exception | None = None
        for p in self.providers:
            try:
                return await p.embed(texts, **kwargs)
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                logger.warning("Embedding 后端 {} 失败: {}", type(p).__name__, exc)
        raise RuntimeError(f"所有 Embedding 后端均失败: {last_exc}")

    async def test(self) -> bool:
        # 只测主后端(本地后端 test 会触发模型下载,较重)
        if not self.providers:
            return False
        try:
            return bool(await self.providers[0].test())
        except Exception:  # noqa: BLE001
            return False


def create_embedding_provider(config: dict[str, Any]) -> EmbeddingProvider | None:
    ptype = (config or {}).get("type", "openai")
    if ptype in ("none", "disabled"):
        return None
    if ptype in ("openai", "openai_compatible"):
        from .sources.embedding_openai import OpenAIEmbeddingProvider

        openai_p = OpenAIEmbeddingProvider(config or {})
        # 默认开启本地回退,增强适配度;显式 fallback_local: false 可关闭
        if (config or {}).get("fallback_local", True):
            from .sources.embedding_local import LocalEmbeddingProvider

            return ChainedEmbeddingProvider([openai_p, LocalEmbeddingProvider({})])
        return openai_p
    if ptype in ("local", "sentence-transformers", "sentence_transformers"):
        from .sources.embedding_local import LocalEmbeddingProvider

        return LocalEmbeddingProvider(config or {})
    raise ValueError(f"未知 Embedding Provider: {ptype}")


def create_rerank_provider(config: dict[str, Any]) -> RerankProvider:
    ptype = (config or {}).get("type", "none")
    if ptype in ("none", "disabled"):
        return None  # type: ignore[return-value]
    if ptype in ("cohere",):
        from .sources.rerank_cohere import CohereRerankProvider

        return CohereRerankProvider(config or {})
    raise ValueError(f"未知 Rerank Provider: {ptype}")
