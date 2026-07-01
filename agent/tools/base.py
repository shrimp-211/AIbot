"""工具基类 — 参考 Claude Code Tool 接口设计.

每个 Tool 代表 LLM 可以调用的一种能力。
工具通过 ToolRegistry 注册，以 OpenAI Function Calling 格式暴露给 LLM。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class BaseTool(ABC):
    """工具基类.

    子类需要实现:
    - name: 工具名称 (英文，LLM 用)
    - description: 工具描述 (LLM 可见)
    - parameters: JSON Schema 参数定义
    - execute(): 异步执行方法
    """

    name: str = ""
    """工具唯一标识符"""

    description: str = ""
    """工具功能描述 (LLM 可见)"""

    permission_level: int = 0
    """所需权限级别: 0=所有人, 1=信任用户, 7=管理员"""

    @property
    @abstractmethod
    def parameters(self) -> dict[str, Any]:
        """OpenAI Function Calling 格式的参数 Schema."""
        ...

    @abstractmethod
    async def execute(self, **kwargs) -> Any:
        """执行工具调用.

        Args:
            **kwargs: 工具参数 (由 LLM 填入)

        Returns:
            工具执行结果 (字符串或可序列化对象)
        """
        ...

    def to_openai_tool(self) -> dict:
        """生成 OpenAI Function Calling 格式的工具定义."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def __repr__(self) -> str:
        return f"<Tool {self.name}>"
