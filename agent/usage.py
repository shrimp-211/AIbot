"""LLM 用量与成本统计(参照 AstrBot / Claude Code 的 token 计价)。

provider 每次 chat 返回的 usage 经 AgentEngine 记录到 JsonKV,提供:
- totals / by_user 累计 token 与估算成本
- history 最近 N 条调用记录
- 按模型价格表估算成本(config cost.prices 可覆盖,单位:美元/百万 token)
"""
from __future__ import annotations

import time
from typing import Any

# 默认价格表:每 100 万 token 的 (输入价, 输出价),按模型名子串匹配,越长 key 越优先。
_DEFAULT_PRICES: dict[str, tuple[float, float]] = {
    "opus": (15.0, 75.0),
    "sonnet": (3.0, 15.0),
    "haiku": (0.8, 4.0),
    "claude": (1.0, 3.0),
    "deepseek": (0.27, 1.1),
    "gpt-4o": (2.5, 10.0),
    "gpt-4": (10.0, 30.0),
    "o1": (15.0, 60.0),
    "o3": (15.0, 60.0),
    "glm": (0.5, 2.0),
    "qwen": (0.5, 1.5),
    "kimi": (0.6, 2.0),
    "moonshot": (0.6, 2.0),
    "gemini": (1.25, 5.0),
    "llama": (0.15, 0.4),
    "mistral": (0.3, 0.8),
}
_DEFAULT_PRICE = (1.0, 3.0)

_HISTORY_LIMIT = 100


class UsageTracker:
    def __init__(self, db: Any, prices: dict[str, Any] | None = None):
        self._db = db
        self._prices = dict(_DEFAULT_PRICES)
        if prices:
            for k, v in prices.items():
                if isinstance(v, (list, tuple)) and len(v) == 2:
                    try:
                        self._prices[str(k)] = (float(v[0]), float(v[1]))
                    except (TypeError, ValueError):
                        continue

    # ---------- 内部 ----------

    def _load(self) -> dict:
        return dict(self._db.get("usage", {}) or {})

    def _save(self, data: dict) -> None:
        self._db.set("usage", data)

    def _match_price(self, model: str) -> tuple[float, float]:
        m = (model or "").lower()
        for key in sorted(self._prices, key=len, reverse=True):
            if key in m:
                return self._prices[key]
        return _DEFAULT_PRICE

    # ---------- 记录 ----------

    def record(self, usage: dict | None, *, user_id: str = "", model: str = "") -> None:
        """累计一次 LLM 调用的 token 用量与估算成本。空用量(如 mock)自动跳过。"""
        usage = usage or {}
        prompt = int(usage.get("prompt_tokens", 0) or 0)
        completion = int(usage.get("completion_tokens", 0) or 0)
        if prompt + completion <= 0:
            return
        in_price, out_price = self._match_price(model)
        cost = (prompt * in_price + completion * out_price) / 1_000_000

        data = self._load()
        totals = data.setdefault(
            "totals", {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0, "estimated_cost": 0.0}
        )
        totals["prompt_tokens"] = totals.get("prompt_tokens", 0) + prompt
        totals["completion_tokens"] = totals.get("completion_tokens", 0) + completion
        totals["calls"] = totals.get("calls", 0) + 1
        totals["estimated_cost"] = totals.get("estimated_cost", 0.0) + cost

        if user_id:
            per_user = data.setdefault("by_user", {}).setdefault(
                user_id,
                {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0, "estimated_cost": 0.0},
            )
            per_user["prompt_tokens"] = per_user.get("prompt_tokens", 0) + prompt
            per_user["completion_tokens"] = per_user.get("completion_tokens", 0) + completion
            per_user["calls"] = per_user.get("calls", 0) + 1
            per_user["estimated_cost"] = per_user.get("estimated_cost", 0.0) + cost

        history = data.setdefault("history", [])
        history.append(
            {
                "ts": int(time.time()),
                "user_id": user_id,
                "model": model,
                "prompt_tokens": prompt,
                "completion_tokens": completion,
                "cost": round(cost, 6),
            }
        )
        data["history"] = history[-_HISTORY_LIMIT:]
        self._save(data)

    # ---------- 查询 / 重置 ----------

    def summary(self, user_id: str = "") -> dict:
        """返回累计用量摘要。user_id 为空=全局,否则=该用户。"""
        data = self._load()
        if user_id:
            s = dict(data.get("by_user", {}).get(user_id, {}))
            prompt = s.get("prompt_tokens", 0)
            completion = s.get("completion_tokens", 0)
            return {
                "calls": s.get("calls", 0),
                "prompt_tokens": prompt,
                "completion_tokens": completion,
                "total_tokens": prompt + completion,
                "estimated_cost": round(s.get("estimated_cost", 0.0), 4),
                "history": [],
            }
        totals = data.get("totals", {})
        prompt = totals.get("prompt_tokens", 0)
        completion = totals.get("completion_tokens", 0)
        return {
            "calls": totals.get("calls", 0),
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": prompt + completion,
            "estimated_cost": round(totals.get("estimated_cost", 0.0), 4),
            "history": data.get("history", [])[-10:],
            "by_user": data.get("by_user", {}),
        }

    def reset(self, user_id: str = "") -> dict:
        """清零统计。user_id 为空=清零全局累计(保留历史记录),否则=清零该用户。"""
        data = self._load()
        if user_id:
            data.get("by_user", {}).pop(user_id, None)
        else:
            data["totals"] = {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0, "estimated_cost": 0.0}
            data["by_user"] = {}
            data["history"] = []
        self._save(data)
        return {"ok": True, "scope": user_id or "all"}
