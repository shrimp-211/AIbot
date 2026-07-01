"""模型能力注册表 — 智能路由多模态请求到原生多模态模型.

已知模型原生能力矩阵 (基于 2026 官方文档):

Vision (图片理解):
- Claude 3.5+ / Claude 4.x (所有) — ✅ 原生
- GPT-4o / GPT-4o-mini / GPT-5 — ✅ 原生
- Gemini 2.5 Pro/Flash — ✅ 原生
- Gemini 2.0 Flash — ✅ 原生
- Qwen-VL 系列 — ✅ 原生
- DeepSeek-V3 — ❌ 不支持
- DeepSeek-R1 — ❌ 不支持 (thinking model)
- Ollama (llama-vision/llava/moondream) — ✅ 原生
- GPT-4-turbo — ❌ 不支持

Audio (语音理解/STT):
- GPT-4o (音频输入) — ✅ 原生
- Gemini 2.5 Flash — ✅ 原生
- Whisper (专用 STT) — ✅

Video:
- Gemini 2.5 Pro/Flash — ✅ 原生
- GPT-4o — ✅ 部分支持

Fallback 策略:
文本模型 → 先用 Vision 模型描述 → 将描述传给文本模型处理
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any

from loguru import logger


class Capability(Enum):
    TEXT = auto()            # 文本 (所有模型都支持)
    VISION = auto()          # 图片理解
    AUDIO_INPUT = auto()     # 音频输入 (原生语音理解)
    AUDIO_OUTPUT = auto()    # 音频输出 (TTS)
    VIDEO = auto()           # 视频理解
    FUNCTION_CALLING = auto()  # 工具调用
    STREAMING = auto()       # 流式输出
    CODING = auto()          # 代码生成优化


@dataclass
class ModelSpec:
    """模型规格 — 包含能力标注和优先级."""
    id: str                             # 唯一标识
    name: str                           # 显示名
    provider: str = "openai"            # 提供商类型
    capabilities: set[Capability] = field(default_factory=lambda: {Capability.TEXT})
    cost_per_1k_input: float = 0.0      # 每 1k 输入 token 成本
    cost_per_1k_output: float = 0.0     # 每 1k 输出 token 成本
    priority: int = 5                   # 优先级 (1=最快最便宜, 10=最强大)
    max_tokens: int = 4096
    vision_detail_level: str = "auto"   # low | high | auto
    supports_system_prompt: bool = True
    rate_limit_rpm: int = 100           # 每分钟请求限制
    notes: str = ""

    @property
    def has_vision(self) -> bool:
        return Capability.VISION in self.capabilities

    @property
    def has_audio(self) -> bool:
        return Capability.AUDIO_INPUT in self.capabilities

    @property
    def has_video(self) -> bool:
        return Capability.VIDEO in self.capabilities


# 2026 年主流模型能力矩阵
MODEL_SPECS: dict[str, ModelSpec] = {
    # ---- Anthropic ----
    "claude-sonnet-4-6": ModelSpec(
        id="claude-sonnet-4-6", name="Claude Sonnet 4.6", provider="anthropic",
        capabilities={Capability.TEXT, Capability.VISION, Capability.FUNCTION_CALLING, Capability.CODING},
        cost_per_1k_input=0.003, cost_per_1k_output=0.015, priority=6,
        max_tokens=8192, notes="推荐默认模型 — 性价比最优",
    ),
    "claude-opus-4-7": ModelSpec(
        id="claude-opus-4-7", name="Claude Opus 4.7", provider="anthropic",
        capabilities={Capability.TEXT, Capability.VISION, Capability.FUNCTION_CALLING, Capability.CODING},
        cost_per_1k_input=0.015, cost_per_1k_output=0.075, priority=9,
        max_tokens=8192, notes="最强大的编程和推理模型",
    ),
    "claude-haiku-4-5": ModelSpec(
        id="claude-haiku-4-5", name="Claude Haiku 4.5", provider="anthropic",
        capabilities={Capability.TEXT, Capability.VISION, Capability.FUNCTION_CALLING},
        cost_per_1k_input=0.0008, cost_per_1k_output=0.004, priority=3,
        max_tokens=4096, notes="最快速最便宜的模型",
    ),

    # ---- OpenAI ----
    "gpt-4o": ModelSpec(
        id="gpt-4o", name="GPT-4o", provider="openai",
        capabilities={Capability.TEXT, Capability.VISION, Capability.AUDIO_INPUT,
                      Capability.FUNCTION_CALLING, Capability.CODING},
        cost_per_1k_input=0.0025, cost_per_1k_output=0.01, priority=7,
        max_tokens=4096, notes="原生多模态: 文字+图片+音频",
    ),
    "gpt-4o-mini": ModelSpec(
        id="gpt-4o-mini", name="GPT-4o Mini", provider="openai",
        capabilities={Capability.TEXT, Capability.VISION, Capability.FUNCTION_CALLING},
        cost_per_1k_input=0.00015, cost_per_1k_output=0.0006, priority=2,
        max_tokens=4096, notes="最便宜的视觉模型",
    ),
    "gpt-5": ModelSpec(
        id="gpt-5", name="GPT-5", provider="openai",
        capabilities={Capability.TEXT, Capability.VISION, Capability.AUDIO_INPUT,
                      Capability.VIDEO, Capability.FUNCTION_CALLING, Capability.CODING},
        cost_per_1k_input=0.005, cost_per_1k_output=0.025, priority=10,
        max_tokens=8192, notes="全模态旗舰模型",
    ),

    # ---- Google ----
    "gemini-2.5-pro": ModelSpec(
        id="gemini-2.5-pro", name="Gemini 2.5 Pro", provider="openai",
        capabilities={Capability.TEXT, Capability.VISION, Capability.AUDIO_INPUT,
                      Capability.VIDEO, Capability.FUNCTION_CALLING, Capability.CODING},
        cost_per_1k_input=0.0035, cost_per_1k_output=0.0175, priority=8,
        max_tokens=8192, notes="原生全模态: 文字+图片+音频+视频",
    ),
    "gemini-2.5-flash": ModelSpec(
        id="gemini-2.5-flash", name="Gemini 2.5 Flash", provider="openai",
        capabilities={Capability.TEXT, Capability.VISION, Capability.AUDIO_INPUT,
                      Capability.FUNCTION_CALLING},
        cost_per_1k_input=0.0005, cost_per_1k_output=0.0025, priority=4,
        max_tokens=4096, notes="快速多模态模型",
    ),

    # ---- DeepSeek ----
    "deepseek-v3": ModelSpec(
        id="deepseek-v3", name="DeepSeek V3", provider="openai",
        capabilities={Capability.TEXT, Capability.FUNCTION_CALLING, Capability.CODING},
        cost_per_1k_input=0.00027, cost_per_1k_output=0.0011, priority=5,
        max_tokens=4096, notes="文本专用 — 不支持视觉",
    ),
    "deepseek-r1": ModelSpec(
        id="deepseek-r1", name="DeepSeek R1", provider="openai",
        capabilities={Capability.TEXT, Capability.CODING},
        cost_per_1k_input=0.00055, cost_per_1k_output=0.00219, priority=7,
        max_tokens=8192, notes="推理专用 — 不支持工具调用和视觉",
    ),

    # ---- Qwen ----
    "qwen-vl-max": ModelSpec(
        id="qwen-vl-max", name="Qwen VL Max", provider="openai",
        capabilities={Capability.TEXT, Capability.VISION, Capability.FUNCTION_CALLING},
        cost_per_1k_input=0.003, cost_per_1k_output=0.009, priority=6,
        max_tokens=4096, notes="中文视觉理解最强",
    ),

    # ---- 开源/本地 ----
    "ollama-llava": ModelSpec(
        id="ollama-llava", name="Ollama LLaVA", provider="openai",
        capabilities={Capability.TEXT, Capability.VISION},
        cost_per_1k_input=0.0, cost_per_1k_output=0.0, priority=1,
        max_tokens=2048, notes="本地视觉模型 — 免费但能力有限",
    ),
    "ollama-llama3.2-vision": ModelSpec(
        id="ollama-llama3.2-vision", name="Ollama Llama 3.2 Vision", provider="openai",
        capabilities={Capability.TEXT, Capability.VISION},
        cost_per_1k_input=0.0, cost_per_1k_output=0.0, priority=2,
        max_tokens=2048, notes="本地视觉模型 — Meta 官方",
    ),
}


class ModelRouter:
    """多模态模型智能路由器.

    根据请求的需求自动选择最合适的模型:
    - 有图片 → 优先 Vision 模型
    - 有音频 → 优先 Audio 模型
    - 纯文本 → 根据复杂度/成本选择
    - 兜底: 用 Vision 模型描述 → 传给文本模型

    选择优先级:
    1. 能力匹配度 (必须满足所需能力)
    2. 优先级 (模型质量)
    3. 成本 (同能力下选择便宜的)
    """

    def __init__(self):
        self._specs: dict[str, ModelSpec] = {}
        self._active_model: str = "claude-sonnet-4-6"

    def register(self, spec: ModelSpec) -> None:
        self._specs[spec.id] = spec

    def register_all(self, specs: dict[str, ModelSpec]) -> None:
        self._specs.update(specs)

    def get_spec(self, model_id: str) -> ModelSpec | None:
        return self._specs.get(model_id)

    @property
    def active_model(self) -> str:
        return self._active_model

    def set_active(self, model_id: str) -> None:
        if model_id in self._specs:
            self._active_model = model_id
            logger.info(f"主模型切换: {model_id}")

    # ---- 智能路由 ----

    def route(
        self,
        required: set[Capability] | None = None,
        preferred_provider: str = "",
        max_cost_input: float = float("inf"),
        fallback_to_describe: bool = True,
    ) -> ModelSpec | None:
        """根据需求路由到最佳模型.

        Args:
            required: 必需的能力 (如 {Capability.VISION})
            preferred_provider: 偏好的提供商
            max_cost_input: 最大输入成本
            fallback_to_describe: 是否允许用 Vision 模型描述后传给文本模型

        Returns:
            最优 ModelSpec，或 None
        """
        if required is None:
            required = {Capability.TEXT}

        candidates = []

        for spec in self._specs.values():
            # 检查必需能力
            if not required.issubset(spec.capabilities):
                continue
            # 成本限制
            if spec.cost_per_1k_input > max_cost_input:
                continue
            candidates.append(spec)

        if not candidates:
            # 没有模型完全满足 → 兜底策略
            if fallback_to_describe and Capability.VISION in required:
                logger.info("无多模态模型可用，启用兜底: Vision 模型 → 文本模型")
                return self._fallback_route(required)
            return None

        # 排序: 提供商偏好 > 优先级 > 成本
        def sort_key(spec: ModelSpec) -> tuple:
            provider_match = 0 if (preferred_provider and spec.provider == preferred_provider) else 1
            return (provider_match, -spec.priority, spec.cost_per_1k_input)

        candidates.sort(key=sort_key)
        best = candidates[0]

        logger.info(
            f"路由决策: {best.id} (capabilities={[c.name for c in best.capabilities]}, "
            f"cost=${best.cost_per_1k_input}/1k input)"
        )
        return best

    def route_vision(self, preferred: str = "") -> ModelSpec | None:
        """路由图片理解请求."""
        return self.route(
            required={Capability.TEXT, Capability.VISION},
            preferred_provider=preferred,
        )

    def route_audio(self, preferred: str = "") -> ModelSpec | None:
        """路由音频理解请求."""
        return self.route(
            required={Capability.TEXT, Capability.AUDIO_INPUT},
            preferred_provider=preferred,
        )

    def route_video(self, preferred: str = "") -> ModelSpec | None:
        """路由视频理解请求."""
        return self.route(
            required={Capability.TEXT, Capability.VIDEO},
            preferred_provider=preferred,
        )

    def route_text(self, complexity: float = 0.5) -> ModelSpec | None:
        """路由纯文本请求."""
        if complexity < 0.3:
            # 简单: 用最便宜的
            return self.route(
                required={Capability.TEXT},
                max_cost_input=0.001,
            )
        elif complexity < 0.6:
            # 中等: 优先级 + 成本平衡
            return self.route(required={Capability.TEXT, Capability.FUNCTION_CALLING})
        else:
            # 复杂: 用最强的
            return self.route(required={Capability.TEXT, Capability.FUNCTION_CALLING, Capability.CODING})

    def _fallback_route(self, required: set[Capability]) -> ModelSpec | None:
        """兜底: 用 Vision 模型 + 文本模型配合."""
        # 返回最优的 Vision 模型
        vision_specs = [
            s for s in self._specs.values()
            if Capability.VISION in s.capabilities
        ]
        if vision_specs:
            vision_specs.sort(key=lambda s: (-s.priority, s.cost_per_1k_input))
            return vision_specs[0]
        return None

    # ---- 能力查询 ----

    def get_vision_models(self) -> list[ModelSpec]:
        return [s for s in self._specs.values() if s.has_vision]

    def get_audio_models(self) -> list[ModelSpec]:
        return [s for s in self._specs.values() if s.has_audio]

    def get_video_models(self) -> list[ModelSpec]:
        return [s for s in self._specs.values() if s.has_video]

    def get_multimodal_models(self) -> list[ModelSpec]:
        """获取支持 VISION + AUDIO + VIDEO 的模型."""
        full = {Capability.VISION, Capability.AUDIO_INPUT, Capability.VIDEO}
        return [s for s in self._specs.values() if full.issubset(s.capabilities)]

    def describe_capabilities(self) -> dict:
        """生成模型能力总览."""
        return {
            spec.id: {
                "name": spec.name,
                "provider": spec.provider,
                "capabilities": [c.name for c in spec.capabilities],
                "cost_input_1k": spec.cost_per_1k_input,
                "priority": spec.priority,
            }
            for spec in self._specs.values()
        }
