"""工具注册中心 — 管理所有工具的生命周期."""

from __future__ import annotations

from typing import Any

from loguru import logger

from .base import BaseTool


class ToolRegistry:
    """工具注册中心.

    功能:
    - 注册/注销工具
    - 按名称查找工具
    - 生成 OpenAI Function Calling 格式的工具列表
    - 执行工具调用 (权限检查 + 参数校验)
    """

    def __init__(self):
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        """注册一个工具."""
        if tool.name in self._tools:
            logger.warning(f"工具 {tool.name} 已注册，将被覆盖")
        self._tools[tool.name] = tool
        logger.debug(f"工具已注册: {tool.name}")

    def unregister(self, name: str) -> None:
        """注销一个工具."""
        self._tools.pop(name, None)

    def get(self, name: str) -> BaseTool | None:
        """按名称获取工具."""
        return self._tools.get(name)

    def get_descriptions(self) -> list[dict]:
        """获取所有工具的描述信息."""
        return [
            {"name": t.name, "description": t.description}
            for t in self._tools.values()
        ]

    def get_openai_tools(self) -> list[dict] | None:
        """生成 OpenAI Function Calling 格式的工具列表."""
        if not self._tools:
            return None
        return [t.to_openai_tool() for t in self._tools.values()]

    async def execute(
        self,
        name: str,
        args: dict[str, Any],
        user_id: str = "",
        auth=None,
    ) -> str:
        """执行工具调用.

        Args:
            name: 工具名称
            args: 工具参数
            user_id: 调用者 ID
            auth: AuthManager 实例 (用于权限检查)

        Returns:
            工具执行结果字符串
        """
        tool = self._tools.get(name)
        if not tool:
            return f"未知工具: {name}"

        # 权限检查
        if auth is not None and tool.permission_level > 0:
            if not auth.check_permission(user_id, tool.permission_level):
                return f"权限不足: 工具 {name} 需要权限级别 {tool.permission_level}"

        # 执行
        try:
            logger.info(f"执行工具: {name}({args})")
            result = await tool.execute(**args)
            return str(result)
        except Exception as e:
            logger.exception(f"工具执行失败: {name}")
            return f"工具执行错误: {e}"

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: str) -> bool:
        return name in self._tools
