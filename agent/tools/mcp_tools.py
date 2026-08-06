"""MCP 工具适配:动态注册的 MCP 工具 + 服务器管理工具。

- MCPTool:把一个 MCP 工具包装成 Bot 工具,名称为 `mcp:<server>_<tool>`,
  由 main.py 在 MCP 服务器启动后动态注册到 ToolRegistry。
- MCPServerListTool:`mcp_list` 列出所有已连接的 MCP 服务器与工具。
"""
from __future__ import annotations

from typing import Any

from .base import Tool, ToolContext


class MCPTool(Tool):
    """适配单个 MCP 工具的动态工具。"""

    def __init__(self, server_name: str, tool_schema: dict[str, Any], permission_level: int = 1):
        self._server_name = server_name
        self._tool_name = tool_schema.get("name", "")
        self.name = f"mcp:{server_name}_{self._tool_name}"
        self.description = tool_schema.get("description", "") or f"MCP 工具 {self._tool_name}"
        raw_params = tool_schema.get("inputSchema", {}) or {}
        self.parameters = {
            "type": "object",
            "properties": (raw_params.get("properties") if isinstance(raw_params, dict) else None)
            or {},
        }
        self.permission_level = permission_level

    async def execute(self, ctx: ToolContext, **kwargs: Any) -> Any:
        mgr = ctx.extra.get("mcp_manager")
        if mgr is None:
            return "MCP 管理器不可用"
        server = mgr.get_server(self._server_name)
        if server is None:
            return f"MCP 服务器 {self._server_name} 未启动"
        if not server.running:
            return f"MCP 服务器 {self._server_name} 未连接"
        try:
            return await server.call_tool(self._tool_name, kwargs)
        except Exception as exc:  # noqa: BLE001
            return f"MCP 工具调用失败: {type(exc).__name__}: {exc}"


class MCPServerListTool(Tool):
    name = "mcp_list"
    description = "列出所有已连接的 MCP(Model Context Protocol)服务器及其可用工具。"
    parameters = {"type": "object", "properties": {}}

    async def execute(self, ctx: ToolContext) -> Any:
        mgr = ctx.extra.get("mcp_manager")
        if mgr is None:
            return "MCP 管理器未初始化,请检查配置 mcp.servers"
        statuses = mgr.list_status()
        if not statuses:
            return "当前没有已连接的 MCP 服务器。配置 mcp.servers 后可接入外部工具生态。"
        lines = []
        for st in statuses:
            tool_list = ", ".join(st.get("tools", [])) or "(无)"
            state = "运行中" if st.get("running") else "已停止"
            lines.append(f"- {st['name']} [{state}] {st.get('command', '')}")
            lines.append(f"  工具: {tool_list}")
        return "\n".join(lines)
