"""2026 主流模型能力矩阵:按任务路由到合适模型(参照 AstrBot ModelRegistry)。

- best_for(task): 按任务类型给出能力要求 → 匹配模型
- 供 orchestrator cost_aware / engine 动态换模使用
"""
from __future__ import annotations

# 能力维度:vision(图像输入) audio(音频) reasoning(深度推理) speed(快速) cost(低成本)
# 每项: {"key": 匹配子串, "capabilities": {...}, "cost_per_1k": 美元}
_MODELS = [
    {"key": "gpt-4o", "cap": {"vision": True, "reasoning": True, "cost": 0.005}},
    {"key": "gpt-4.1", "cap": {"vision": True, "reasoning": True, "cost": 0.004}},
    {"key": "gpt-4.5", "cap": {"vision": True, "reasoning": True, "cost": 0.012}},
    {"key": "claude-sonnet", "cap": {"vision": True, "reasoning": True, "cost": 0.006}},
    {"key": "claude-opus", "cap": {"vision": True, "reasoning": True, "cost": 0.015}},
    {"key": "deepseek-r1", "cap": {"reasoning": True, "cost": 0.0005}},
    {"key": "deepseek", "cap": {"speed": True, "cost": 0.0003}},
    {"key": "qwen2.5-vl", "cap": {"vision": True, "cost": 0.001}},
    {"key": "qwen", "cap": {"cost": 0.001}},
    {"key": "glm", "cap": {"cost": 0.001}},
    {"key": "kimi", "cap": {"cost": 0.001}},
    {"key": "moonshot", "cap": {"cost": 0.001}},
    {"key": "gemini", "cap": {"vision": True, "reasoning": True, "cost": 0.002}},
]

_TASK_REQUIREMENTS = {
    "vision": {"vision": True},
    "image": {"vision": True},
    "reasoning": {"reasoning": True},
    "math": {"reasoning": True},
    "coding": {"reasoning": True},
    "fast": {"speed": True},
    "cheap": {"cost": True},
    "summary": {"cost": True},
    "chat": {},
}


class ModelRegistry:
    """模型能力矩阵。"""

    @staticmethod
    def capabilities(model: str) -> dict:
        m = (model or "").lower()
        for entry in _MODELS:
            if entry["key"] in m:
                return dict(entry["cap"])
        return {}

    @staticmethod
    def best_for(task: str, available: list[str]) -> str | None:
        """按任务要求从可用模型中选最优;无要求时返回成本最低的。"""
        task = (task or "").lower()
        req = _TASK_REQUIREMENTS.get(task, {})
        if not req:
            return ModelRegistry._cheapest(available)
        scored = []
        for m in available:
            cap = ModelRegistry.capabilities(m)
            # 满足所有要求才候选;得分 = 满足项数 - 成本
            if all(cap.get(k) for k in req):
                score = len(req) - cap.get("cost", 1)
                scored.append((score, cap.get("cost", 1), m))
        if not scored:
            return None
        scored.sort(key=lambda x: (-x[0], x[1]))
        return scored[0][2]

    @staticmethod
    def _cheapest(available: list[str]) -> str | None:
        if not available:
            return None
        return min(available, key=lambda m: ModelRegistry.capabilities(m).get("cost", 1))
