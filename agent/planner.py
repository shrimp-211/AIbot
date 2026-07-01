"""规划器 — 任务分解与反思评估.

参考 Claude Code / mainidea 的规划与推理设计。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class TaskStep:
    """单个任务步骤."""
    id: int
    description: str
    status: str = "pending"  # pending | running | done | failed
    result: str = ""


@dataclass
class TaskPlan:
    """任务计划 — DAG 的简化实现 (顺序步骤)."""
    goal: str
    steps: list[TaskStep] = field(default_factory=list)
    current_step: int = 0

    def add_step(self, description: str) -> "TaskPlan":
        step = TaskStep(id=len(self.steps) + 1, description=description)
        self.steps.append(step)
        return self

    def next(self) -> TaskStep | None:
        if self.current_step < len(self.steps):
            step = self.steps[self.current_step]
            self.current_step += 1
            return step
        return None

    def all_done(self) -> bool:
        return self.current_step >= len(self.steps)

    def summary(self) -> str:
        lines = [f"目标: {self.goal}"]
        for s in self.steps:
            icon = "✅" if s.status == "done" else "⏳" if s.status == "running" else "⬜"
            lines.append(f"  {icon} 步骤 {s.id}: {s.description}")
        return "\n".join(lines)


class Planner:
    """简单的任务规划器."""

    @staticmethod
    def create_plan(goal: str, steps: list[str]) -> TaskPlan:
        """创建任务计划."""
        plan = TaskPlan(goal=goal)
        for step_desc in steps:
            plan.add_step(step_desc)
        return plan

    @staticmethod
    async def reflect(result: str, expected: str = "") -> dict:
        """反思评估 — 简单的质量检查.

        Returns:
            包含评估结果的字典
        """
        issues = []

        if not result:
            issues.append("结果为空")
        if len(result) < 10 and expected:
            issues.append("结果过于简短")

        if "错误" in result or "失败" in result:
            issues.append("结果包含错误信息")

        return {
            "quality": "low" if issues else "ok",
            "issues": issues,
            "needs_retry": len(issues) > 0,
        }
