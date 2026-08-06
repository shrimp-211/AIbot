"""多 API 编排器(参照 AstrBot Orchestrator + mainidea 多 API 协同调度)。

6 种模式:
- parallel:   并行调用,取最快非空结果
- race:       竞速,首个完成即返回(不论质量)
- vote:       多路投票,取多数一致的 content(简单一致性判断)
- fusion:     融合,拼接多路结果(各 provider 视角)
- fallback:   顺序降级,首个成功返回
- cost_aware: 按成本路由,低成本模型优先,失败升级

记录每 provider 的延迟/错误/成本,供 WebUI 展示与后续调优。
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

from loguru import logger

MODES = ("parallel", "race", "vote", "fusion", "fallback", "cost_aware")


class Orchestrator:
    """多 provider 编排器。"""

    def __init__(self, providers: list[Any]):
        self.providers = [p for p in providers if p is not None]
        # provider index -> {calls, errors, latency_ms, cost, last_error}
        self.stats: dict[int, dict[str, Any]] = {}
        for i in range(len(self.providers)):
            self.stats[i] = {"calls": 0, "errors": 0, "latency_ms": [], "cost": 0.0, "last_error": ""}

    def _record(self, i: int, latency: float, error: str | None = None) -> None:
        s = self.stats[i]
        s["calls"] += 1
        s["latency_ms"].append(int(latency * 1000))
        s["latency_ms"] = s["latency_ms"][-50:]  # 防无限增长
        if error:
            s["errors"] += 1
            s["last_error"] = error

    def _estimate_cost(self, provider: Any, usage: dict) -> float:
        """粗略成本估算(美元):按 token 数 × 假设单价。"""
        try:
            prompt = int(usage.get("prompt_tokens", 0) or 0)
            completion = int(usage.get("completion_tokens", 0) or 0)
            name = (getattr(provider, "model", "") or "").lower()
            per_1k = 0.002 if "gpt-4" in name or "claude" in name else 0.0005
            return (prompt + completion) * per_1k / 1000.0
        except Exception:  # noqa: BLE001
            return 0.0

    async def call(
        self,
        mode: str,
        messages: list[dict],
        system_prompt: str | None = None,
        tools: list[dict] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        mode = (mode or "fallback").lower()
        if mode not in MODES:
            raise ValueError(f"未知编排模式: {mode},可用: {MODES}")
        if not self.providers:
            return {"content": "", "tool_calls": [], "orchestrator": {"mode": mode, "error": "无可用 provider"}}
        if mode == "fallback":
            return await self._fallback(messages, system_prompt, tools, **kwargs)
        if mode == "parallel":
            return await self._parallel(messages, system_prompt, tools, **kwargs)
        if mode == "race":
            return await self._race(messages, system_prompt, tools, **kwargs)
        if mode == "vote":
            return await self._vote(messages, system_prompt, tools, **kwargs)
        if mode == "fusion":
            return await self._fusion(messages, system_prompt, tools, **kwargs)
        return await self._cost_aware(messages, system_prompt, tools, **kwargs)

    # ---------- 各模式 ----------

    async def _invoke(self, i: int, messages, system_prompt, tools, **kw) -> tuple[int, dict]:
        provider = self.providers[i]
        t0 = time.monotonic()
        try:
            result = await provider.chat(messages, system_prompt=system_prompt, tools=tools, **kw)
            self._record(i, time.monotonic() - t0)
            self.stats[i]["cost"] += self._estimate_cost(provider, result.get("usage", {}))
            return i, result
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            self._record(i, time.monotonic() - t0, str(exc))
            logger.warning("provider {} 调用失败: {}", i, exc)
            return i, {"content": "", "tool_calls": [], "error": str(exc)}

    async def _fallback(self, messages, system_prompt, tools, **kw) -> dict:
        last: dict = {}
        for i in range(len(self.providers)):
            _, result = await self._invoke(i, messages, system_prompt, tools, **kw)
            if result.get("content") or result.get("tool_calls"):
                result["orchestrator"] = {"mode": "fallback", "provider": i}
                return result
            last = result
        return {"content": "", "tool_calls": [], "orchestrator": {"mode": "fallback", "error": last.get("error", "全部失败")}}

    async def _parallel(self, messages, system_prompt, tools, **kw) -> dict:
        """并行调用,取最快返回非空结果的 provider。"""
        results = await asyncio.gather(
            *[self._invoke(i, messages, system_prompt, tools, **kw) for i in range(len(self.providers))]
        )
        for i, result in results:
            if result.get("content") or result.get("tool_calls"):
                result["orchestrator"] = {"mode": "parallel", "provider": i}
                return result
        return {"content": "", "tool_calls": [], "orchestrator": {"mode": "parallel", "error": "全部失败"}}

    async def _race(self, messages, system_prompt, tools, **kw) -> dict:
        """竞速:取首个完成的非空结果。"""
        pending = [asyncio.ensure_future(self._invoke(i, messages, system_prompt, tools, **kw)) for i in range(len(self.providers))]
        for fut in asyncio.as_completed(pending):
            i, result = await fut
            if result.get("content") or result.get("tool_calls"):
                for f in pending:
                    if not f.done():
                        f.cancel()
                result["orchestrator"] = {"mode": "race", "provider": i}
                return result
        return {"content": "", "tool_calls": [], "orchestrator": {"mode": "race", "error": "全部失败"}}

    async def _vote(self, messages, system_prompt, tools, **kw) -> dict:
        """投票:取多数一致的 content;无一致时取最长结果。"""
        results = await asyncio.gather(
            *[self._invoke(i, messages, system_prompt, tools, **kw) for i in range(len(self.providers))]
        )
        contents = [r.get("content", "") for _, r in results if r.get("content")]
        if not contents:
            return {"content": "", "tool_calls": [], "orchestrator": {"mode": "vote", "error": "全部无内容"}}
        # 简易一致性:归一化后按出现次数投票
        norm = {c: c.replace(" ", "").lower() for c in contents}
        counts: dict[str, int] = {}
        for c in norm.values():
            counts[c] = counts.get(c, 0) + 1
        winner_norm = max(counts, key=counts.get)
        if counts[winner_norm] >= 2:
            winner = next(c for c in contents if c.replace(" ", "").lower() == winner_norm)
        else:
            winner = max(contents, key=len)
        return {"content": winner, "tool_calls": [], "orchestrator": {"mode": "vote", "votes": counts[winner_norm]}}

    async def _fusion(self, messages, system_prompt, tools, **kw) -> dict:
        """融合:拼接各 provider 结果(保留各自视角)。"""
        results = await asyncio.gather(
            *[self._invoke(i, messages, system_prompt, tools, **kw) for i in range(len(self.providers))]
        )
        parts = [r.get("content", "").strip() for _, r in results if r.get("content")]
        return {
            "content": "\n\n".join(parts),
            "tool_calls": [],
            "orchestrator": {"mode": "fusion", "sources": len(parts)},
        }

    async def _cost_aware(self, messages, system_prompt, tools, **kw) -> dict:
        """成本路由:按累计成本从低到高尝试,首个成功返回。"""
        order = sorted(range(len(self.providers)), key=lambda i: self.stats[i]["cost"])
        last: dict = {}
        for i in order:
            _, result = await self._invoke(i, messages, system_prompt, tools, **kw)
            if result.get("content") or result.get("tool_calls"):
                result["orchestrator"] = {"mode": "cost_aware", "provider": i}
                return result
            last = result
        return {"content": "", "tool_calls": [], "orchestrator": {"mode": "cost_aware", "error": last.get("error", "全部失败")}}

    def summary(self) -> dict:
        return {
            "providers": len(self.providers),
            "stats": {
                str(i): {
                    "calls": s["calls"],
                    "errors": s["errors"],
                    "avg_latency_ms": int(sum(s["latency_ms"]) / len(s["latency_ms"])) if s["latency_ms"] else 0,
                    "cost": round(s["cost"], 5),
                    "last_error": s["last_error"][:120],
                }
                for i, s in self.stats.items()
            },
        }
