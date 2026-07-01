"""子代理工具 — AgentTool.

参考 Claude Code 的 AgentTool 实现:
- 支持定义子代理类型 (Explore, General-purpose, 自定义)
- 子代理有独立的 system prompt 和工具白名单
- 可通过 AgentDefinition 定义自定义子代理
- 支持 model 选择和隔离工作树
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .base import BaseTool


@dataclass
class AgentDefinition:
    """子代理定义 — 参考 Claude Code AgentDefinition."""
    name: str
    """唯一标识符"""

    description: str
    """描述 — Claude 用此决定何时委托给此代理"""

    system_prompt: str = ""
    """自定义系统提示词"""

    tools: list[str] | None = None
    """工具白名单 (None = 继承所有只读工具)"""

    disallowed_tools: list[str] | None = None
    """工具黑名单"""

    model: str | None = None
    """覆盖模型 (例如 'haiku' 用于快速任务)"""

    max_turns: int = 10
    """最大执行轮次"""

    permission_level: int = 0
    """调用此代理所需的最低权限级别"""

    background: bool = False
    """是否始终在后台运行"""

    color: str = "cyan"
    """控制台显示颜色"""


class AgentTool(BaseTool):
    """子代理工具 — 创建并运行一个专注的子代理.

    LLM 使用此工具将子任务委派给具有独立上下文、工具限制的子代理。
    子代理独立工作并返回结果摘要。
    """

    name = "agent"
    description = (
        "创建一个专门的子代理来执行子任务。子代理运行在独立的上下文中，"
        "有自己的工具限制。适用场景: 代码搜索、数据分析、独立实现等。"
        "可用代理类型: explore (快速搜索), general-purpose (通用任务)"
    )
    permission_level = 0

    def __init__(self, engine=None, tools=None):
        super().__init__()
        self._engine = engine
        self._tools = tools
        self._definitions: dict[str, AgentDefinition] = {}

        # 注册内置子代理
        self.register_definition(AgentDefinition(
            name="explore",
            description="快速搜索和分析代码库。使用只读工具搜索文件、查找模式、理解代码结构。",
            tools=["file_read", "glob", "grep", "web_search", "web_fetch"],
            max_turns=8,
        ))
        self.register_definition(AgentDefinition(
            name="general-purpose",
            description="通用任务代理。可以执行任意类型的任务，拥有完整的工具访问权限。",
            max_turns=15,
        ))

    def register_definition(self, definition: AgentDefinition) -> None:
        """注册子代理定义."""
        self._definitions[definition.name] = definition

    def set_engine(self, engine) -> None:
        """注入引擎引用 (延迟注入以避免循环依赖)."""
        self._engine = engine
        if hasattr(engine, 'tools'):
            self._tools = engine.tools

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "description": {
                    "type": "string",
                    "description": "简短描述子代理要做什么 (3-5个字)",
                },
                "prompt": {
                    "type": "string",
                    "description": "分配给子代理的任务描述和指令",
                },
                "subagent_type": {
                    "type": "string",
                    "description": "子代理类型: 'explore' (快速搜索), 'general-purpose' (通用), 或自定义名称",
                    "default": "general-purpose",
                },
                "model": {
                    "type": "string",
                    "description": "覆盖使用的模型，如 'haiku' (快速便宜)",
                },
            },
            "required": ["description", "prompt"],
        }

    async def execute(self, description: str, prompt: str, subagent_type: str = "general-purpose", model: str = "", **kwargs) -> str:
        """执行子代理任务."""
        if not self._engine:
            return "子代理系统未初始化"

        if not self._tools:
            self._tools = self._engine.tools

        # 获取代理定义
        definition = self._definitions.get(subagent_type)
        if not definition:
            available = ", ".join(self._definitions.keys())
            return f"未知代理类型: {subagent_type}。可用: {available}"

        # 构建子代理用的工具注册表
        sub_tools = self._build_sub_tools(definition)
        if definition.tools:
            tool_list = ", ".join(definition.tools)
        else:
            tool_list = "全部"

        # 使用引擎的子代理能力
        try:
            result = await self._engine.run_sub_agent(
                prompt=prompt,
                system_prompt=definition.system_prompt,
                tools=sub_tools,
                max_turns=definition.max_turns or 10,
            )
            return f"[子代理: {subagent_type}] 完成:\n{result}"
        except Exception as e:
            return f"[子代理: {subagent_type}] 执行失败: {e}"

    def _build_sub_tools(self, definition: AgentDefinition) -> list:
        """根据定义构建子工具列表."""
        tools = []

        if definition.tools is not None:
            # 白名单模式
            for name in definition.tools:
                tool = self._tools.get(name) if self._tools else None
                if tool:
                    tools.append(tool)
        else:
            # 默认: 只给只读工具
            readonly = ["file_read", "glob", "grep", "web_search", "web_fetch", "task_list", "task_get"]
            for name in readonly:
                tool = self._tools.get(name) if self._tools else None
                if tool:
                    tools.append(tool)

        # 排除黑名单
        if definition.disallowed_tools:
            tools = [t for t in tools if t.name not in definition.disallowed_tools]

        return tools

    def get_definitions(self) -> list[AgentDefinition]:
        return list(self._definitions.values())
