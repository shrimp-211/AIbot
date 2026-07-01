"""多模型并行编排引擎 — 超越 AstrBot 和 Claude Code 的设计.

核心能力 (参考 mainidea.txt 多 API 协同调度层):
1. **并行调用**: 同时调用多个模型，取最快/最优结果
2. **竞速机制**: 同一任务发到多个模型，谁先返回用谁
3. **结果融合**: 多个模型结果交叉验证、投票、拼接
4. **Fallback 链**: A → B → C 保证可用性
5. **成本路由**: 简单任务用小模型，复杂任务用大模型
6. **流式聚合**: 多个流式响应合并输出

示例: 一次请求同时调用
  Claude Opus 4    ─┐
  GPT-4o           ─┼→ 结果融合/投票 → 最优输出
  Gemini 2.5       ─┘
"""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from loguru import logger


class OrchestrationMode(Enum):
    PARALLEL = "parallel"       # 并行调用，取最快
    RACE = "race"               # 竞速: 第一个成功的返回
    VOTE = "vote"               # 投票: 多数结果一致时输出
    FUSION = "fusion"           # 融合: 交叉验证+拼接
    FALLBACK = "fallback"       # 降级链: A→B→C
    COST_AWARE = "cost_aware"   # 成本感知路由


@dataclass
class ModelConfig:
    """模型配置."""
    id: str                      # 标识符
    name: str                    # 模型名
    provider: str = "openai"     # 提供商
    cost_per_1k: float = 0.0     # 每 1k token 成本 (元)
    priority: int = 0            # 优先级 (越小越快)
    capabilities: set[str] = field(default_factory=lambda: {"text"})
    max_tokens: int = 4096


@dataclass
class ModelResponse:
    """单个模型的响应."""
    model: str
    content: str
    latency: float               # 延迟 (秒)
    tokens: int = 0
    cost: float = 0.0
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and bool(self.content)


class ModelOrchestrator:
    """多模型编排引擎.

    支持 6 种编排模式，根据任务类型自动选择最优策略。
    """

    def __init__(self, providers: list = None):
        """
        Args:
            providers: Provider 实例列表
        """
        self._providers: dict[str, Any] = {}
        self._models: dict[str, ModelConfig] = {}
        self._stats: dict[str, dict] = defaultdict(lambda: {"calls": 0, "errors": 0, "total_latency": 0})

        if providers:
            for p in providers:
                self.add_provider(p.model, p)

    def add_provider(self, name: str, provider) -> None:
        self._providers[name] = provider

    def add_model(self, config: ModelConfig) -> None:
        self._models[config.id] = config

    # ---- 核心方法 ----

    async def orchestrate(
        self,
        messages: list[dict],
        mode: OrchestrationMode = OrchestrationMode.PARALLEL,
        models: list[str] | None = None,
        tools: list[dict] | None = None,
        **kwargs,
    ) -> ModelResponse:
        """编排多模型调用.

        Args:
            messages: 消息列表
            mode: 编排模式
            models: 使用的模型列表 (None = all)
            tools: 工具定义

        Returns:
            最终响应
        """
        if models is None:
            models = list(self._providers.keys())

        if len(models) == 1:
            return await self._call_single(models[0], messages, tools)

        logger.info(f"编排器: mode={mode.value}, models={models}")

        if mode == OrchestrationMode.PARALLEL:
            return await self._parallel(messages, models, tools)
        elif mode == OrchestrationMode.RACE:
            return await self._race(messages, models, tools)
        elif mode == OrchestrationMode.VOTE:
            return await self._vote(messages, models, tools)
        elif mode == OrchestrationMode.FUSION:
            return await self._fusion(messages, models, tools)
        elif mode == OrchestrationMode.FALLBACK:
            return await self._fallback(messages, models, tools)
        elif mode == OrchestrationMode.COST_AWARE:
            return await self._cost_aware(messages, models, tools)
        else:
            return await self._parallel(messages, models, tools)

    async def _call_single(self, model_name: str, messages: list[dict], tools: list[dict] | None) -> ModelResponse:
        """调用单个模型."""
        provider = self._providers.get(model_name)
        if not provider:
            return ModelResponse(model=model_name, content="", latency=0, error=f"未知模型: {model_name}")

        t0 = time.time()
        try:
            resp = await provider.chat(messages, tools=tools)
            latency = time.time() - t0
            content = resp.get("content", "")
            self._stats[model_name]["calls"] += 1
            self._stats[model_name]["total_latency"] += latency
            return ModelResponse(
                model=model_name,
                content=content,
                latency=latency,
                tokens=resp.get("usage", {}).get("total_tokens", 0),
            )
        except Exception as e:
            latency = time.time() - t0
            self._stats[model_name]["errors"] += 1
            return ModelResponse(model=model_name, content="", latency=latency, error=str(e))

    async def _parallel(self, messages: list[dict], models: list[str], tools: list[dict] | None) -> ModelResponse:
        """并行调用 — 取最快成功的响应."""
        tasks = [self._call_single(m, messages, tools) for m in models]
        results: list[ModelResponse] = await asyncio.gather(*tasks, return_exceptions=True)

        # 过滤成功响应
        ok = [r for r in results if isinstance(r, ModelResponse) and r.ok]
        if not ok:
            return ModelResponse(model="orchestrator", content="所有模型均调用失败", latency=0, error="all_failed")

        # 返回最快的
        ok.sort(key=lambda r: r.latency)
        best = ok[0]
        logger.info(f"并行: 选择 {best.model} (延迟 {best.latency:.2f}s, 共 {len(ok)}/{len(models)} 成功)")
        return best

    async def _race(self, messages: list[dict], models: list[str], tools: list[dict] | None) -> ModelResponse:
        """竞速 — 第一个返回成功的即用 (any() 语义)."""
        async def race_call(model_name):
            return await self._call_single(model_name, messages, tools)

        tasks = [asyncio.create_task(race_call(m)) for m in models]

        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)

        # 取消未完成的
        for task in pending:
            task.cancel()

        for task in done:
            result = task.result()
            if result.ok:
                logger.info(f"竞速胜出: {result.model} ({result.latency:.2f}s)")
                return result

        return ModelResponse(model="orchestrator", content="竞速失败", latency=0, error="race_failed")

    async def _vote(self, messages: list[dict], models: list[str], tools: list[dict] | None) -> ModelResponse:
        """投票 — 多数模型结果一致时输出."""
        results = await asyncio.gather(*[self._call_single(m, messages, tools) for m in models])

        ok_results = [r for r in results if r.ok]
        if not ok_results:
            return ModelResponse(model="orchestrator", content="", latency=0, error="no_votes")

        if len(ok_results) < 2:
            return ok_results[0]

        # 简单投票: 比较内容相似度
        votes = defaultdict(int)
        best_result = ok_results[0]
        for i, r1 in enumerate(ok_results):
            for j, r2 in enumerate(ok_results):
                if i < j and self._content_similar(r1.content, r2.content):
                    votes[i] += 1
                    votes[j] += 1

        if votes:
            winner_idx = max(votes, key=votes.get)
            best_result = ok_results[winner_idx]
            logger.info(f"投票: 选择 {best_result.model} ({votes[winner_idx]} 票)")

        return best_result

    async def _fusion(self, messages: list[dict], models: list[str], tools: list[dict] | None) -> ModelResponse:
        """融合 — 交叉验证 + 取最佳片段拼接."""
        results = await asyncio.gather(*[self._call_single(m, messages, tools) for m in models])
        ok_results = [r for r in results if r.ok]

        if not ok_results:
            return ModelResponse(model="orchestrator", content="", latency=0, error="fusion_failed")

        if len(ok_results) == 1:
            return ok_results[0]

        # 融合: 取最长的作为基础，附加其他模型的不同观点
        ok_results.sort(key=lambda r: len(r.content), reverse=True)
        base = ok_results[0]

        # 附加其他模型的额外信息
        extra_parts = []
        for r in ok_results[1:]:
            if not self._content_similar(r.content, base.content):
                extra_parts.append(f"[{r.model} 补充]\n{r.content[:300]}")

        if extra_parts:
            base.content = base.content + "\n\n---\n" + "\n\n".join(extra_parts)

        base.model = "fusion:" + "+".join(r.model for r in ok_results)
        logger.info(f"融合: {len(ok_results)} 模型结果已合并")
        return base

    async def _fallback(self, messages: list[dict], models: list[str], tools: list[dict] | None) -> ModelResponse:
        """Fallback 链 — A 失败 → B → C."""
        for model_name in models:
            result = await self._call_single(model_name, messages, tools)
            if result.ok:
                logger.info(f"Fallback: {model_name} 成功")
                return result
            logger.warning(f"Fallback: {model_name} 失败 ({result.error}), 尝试下一个...")

        return ModelResponse(model="orchestrator", content="所有降级链均失败", latency=0, error="fallback_exhausted")

    async def _cost_aware(self, messages: list[dict], models: list[str], tools: list[dict] | None) -> ModelResponse:
        """成本感知路由 — 简单任务用小模型."""
        # 评估任务复杂度
        complexity = self._estimate_complexity(messages)

        if complexity < 0.3:
            # 简单: 用最便宜的模型
            models.sort(key=lambda m: self._models.get(m, ModelConfig(id=m, name=m)).cost_per_1k)
            model = models[0]
            logger.info(f"成本路由 (简单): {model}")
        elif complexity < 0.7:
            # 中等: 用性价比最高的
            logger.info(f"成本路由 (中等): 并行调用 2 个模型取最快")
            return await self._parallel(messages, models[:2], tools)
        else:
            # 复杂: 用最强模型 + 投票
            logger.info(f"成本路由 (复杂): 投票模式")
            return await self._vote(messages, models, tools)

        return await self._call_single(model, messages, tools)

    # ---- 辅助方法 ----

    @staticmethod
    def _content_similar(a: str, b: str, threshold: float = 0.6) -> bool:
        """简单内容相似度判断."""
        if not a or not b:
            return False
        a_words = set(a.lower().split())
        b_words = set(b.lower().split())
        if not a_words or not b_words:
            return False
        common = a_words & b_words
        return len(common) / min(len(a_words), len(b_words)) > threshold

    @staticmethod
    def _estimate_complexity(messages: list[dict]) -> float:
        """评估任务复杂度 (0.0-1.0)."""
        score = 0.0
        for msg in messages:
            content = msg.get("content", "") or ""
            if len(content) > 500:
                score += 0.3
            if "代码" in content or "code" in content.lower():
                score += 0.2
            if "分析" in content or "复杂" in content:
                score += 0.2
            if msg.get("tool_calls"):
                score += 0.3
        return min(score, 1.0)

    def get_stats(self) -> dict:
        """获取统计信息."""
        stats = {}
        for model, s in self._stats.items():
            avg_latency = s["total_latency"] / s["calls"] if s["calls"] > 0 else 0
            stats[model] = {
                "calls": s["calls"],
                "errors": s["errors"],
                "error_rate": s["errors"] / s["calls"] if s["calls"] > 0 else 0,
                "avg_latency_ms": int(avg_latency * 1000),
            }
        return stats
