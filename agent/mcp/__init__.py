"""MCP(Model Context Protocol)客户端支持。

- MCPServer:单服务器 stdio 连接(握手/工具发现/工具调用)
- MCPManager:多服务器管理
"""
from .client import MCPManager, MCPServer

__all__ = ["MCPManager", "MCPServer"]
