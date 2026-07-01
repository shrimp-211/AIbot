"""Plan Mode 工具 — EnterPlanMode + ExitPlanMode (参考Claude Code)."""

from typing import Any
from .base import BaseTool


class EnterPlanModeTool(BaseTool):
    name = "enter_plan_mode"
    description = "进入计划模式。Plan模式下只能探索和读取，不能修改文件或执行写操作。用于先分析再实施的场景。"
    permission_level = 0

    engine = None  # 由main.py注入

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {
            "goal": {"type": "string", "description": "计划目标描述"},
        }, "required": ["goal"]}

    async def execute(self, goal: str, **kwargs) -> str:
        if self.engine:
            self.engine.enter_plan_mode()
        return f"已进入Plan模式。目标: {goal}\n现在你可以探索代码库、搜索文件、阅读文档来制定计划。完成后使用 exit_plan_mode 退出。"


class ExitPlanModeTool(BaseTool):
    name = "exit_plan_mode"
    description = "退出计划模式，返回到正常模式。在Plan模式下收集足够信息后调用此工具来开始实施。"
    permission_level = 0

    engine = None

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {
            "plan": {"type": "string", "description": "实施计划摘要"},
        }, "required": ["plan"]}

    async def execute(self, plan: str, **kwargs) -> str:
        if self.engine:
            self.engine.exit_plan_mode()
        return f"已退出Plan模式。实施计划已记录:\n{plan[:1000]}"
