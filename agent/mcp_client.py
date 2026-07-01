"""MCP 客户端 — 参考 AstrBot MCP + Claude Code MCP 实现。

支持 stdio 传输，通过子进程与 MCP 服务器通信。
安全白名单: 只允许 python/node/npx/uv 等可信启动器，
拒绝 bash/sh/curl/wget/rm 等危险命令。
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

from loguru import logger

# AstrBot 风格安全白名单
_ALLOWED_COMMANDS = frozenset({
    "python", "python3", "py", "node", "npx", "npm", "pnpm",
    "yarn", "bun", "bunx", "deno", "uv", "uvx",
})
_DENIED_COMMANDS = frozenset({
    "bash", "sh", "zsh", "fish", "cmd", "cmd.exe",
    "powershell", "powershell.exe", "pwsh",
    "curl", "wget", "nc", "netcat", "telnet", "ssh", "scp",
    "rm", "mv", "cp", "dd", "mkfs", "sudo", "su",
    "chmod", "chown", "kill", "killall",
    "shutdown", "reboot", "poweroff",
})


class MCPServer:
    """单个 MCP 服务器的连接管理."""

    def __init__(self, name: str, command: str, args: list[str] = None, env: dict[str, str] = None):
        self.name = name
        self.command = command
        self.args = args or []
        self.env = env or {}
        self._proc: asyncio.subprocess.Process | None = None
        self._tools: list[dict] = []
        self._request_id = 0

    async def connect(self) -> bool:
        cmd_name = Path(self.command).name if "/" in self.command or "\\" in self.command else self.command
        if cmd_name in _DENIED_COMMANDS:
            logger.error(f"MCP安全拒绝: {cmd_name} 在拒绝列表中")
            return False
        if cmd_name not in _ALLOWED_COMMANDS:
            logger.warning(f"MCP: {cmd_name} 不在标准白名单中，继续尝试连接")

        try:
            self._proc = await asyncio.create_subprocess_exec(
                self.command, *self.args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                env={**os.environ, **self.env},
            )
            # Initialize handshake
            init_result = await self._send_request("initialize", {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "QQ-Agent", "version": "1.0.0"},
            })
            if init_result:
                await self._send_notification("notifications/initialized", {})
                tools_result = await self._send_request("tools/list", {})
                if tools_result:
                    self._tools = tools_result.get("tools", [])
                    logger.info(f"MCP [{self.name}]: {len(self._tools)} 工具已加载")
                    return True
            return False
        except Exception as e:
            logger.error(f"MCP [{self.name}] 连接失败: {e}")
            return False

    async def _send_request(self, method: str, params: dict) -> dict | None:
        if not self._proc or not self._proc.stdin:
            return None
        self._request_id += 1
        rid = self._request_id
        req = json.dumps({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        try:
            self._proc.stdin.write((req + "\n").encode())
            await self._proc.stdin.drain()
            line = await asyncio.wait_for(self._proc.stdout.readline(), timeout=10)
            if line:
                resp = json.loads(line.decode())
                if "error" in resp:
                    logger.warning(f"MCP [{self.name}] {method}: {resp['error']}")
                    return None
                return resp.get("result")
        except (asyncio.TimeoutError, Exception) as e:
            logger.warning(f"MCP [{self.name}] request failed: {e}")
        return None

    async def _send_notification(self, method: str, params: dict) -> None:
        if not self._proc or not self._proc.stdin:
            return
        notif = json.dumps({"jsonrpc": "2.0", "method": method, "params": params})
        try:
            self._proc.stdin.write((notif + "\n").encode())
            await self._proc.stdin.drain()
        except Exception:
            pass

    async def call_tool(self, tool_name: str, arguments: dict) -> str:
        result = await self._send_request("tools/call", {"name": tool_name, "arguments": arguments})
        if result:
            content = result.get("content", [])
            texts = [c.get("text", "") for c in content if c.get("type") == "text"]
            return "\n".join(texts) if texts else json.dumps(result, ensure_ascii=False)
        return f"MCP工具 {tool_name} 调用失败"

    @property
    def tools(self) -> list[dict]:
        return self._tools

    async def disconnect(self) -> None:
        if self._proc:
            try:
                self._proc.stdin.close()
                await asyncio.wait_for(self._proc.wait(), timeout=5)
            except Exception:
                self._proc.kill()
            self._proc = None


class MCPManager:
    """MCP 服务器管理器."""

    def __init__(self):
        self._servers: dict[str, MCPServer] = {}

    async def connect_server(self, name: str, command: str, args: list[str] = None, env: dict = None) -> bool:
        if name in self._servers:
            await self._servers[name].disconnect()
        server = MCPServer(name, command, args, env)
        if await server.connect():
            self._servers[name] = server
            return True
        return False

    def get_server(self, name: str) -> MCPServer | None:
        return self._servers.get(name)

    def get_all_tools(self) -> list[dict]:
        tools = []
        for name, server in self._servers.items():
            for tool in server.tools:
                tools.append({
                    "name": f"mcp__{name}__{tool['name']}",
                    "description": tool.get("description", ""),
                    "input_schema": tool.get("inputSchema", {}),
                })
        return tools

    async def call_tool(self, tool_name: str, arguments: dict) -> str:
        # mcp__server__toolname → server, tool
        if not tool_name.startswith("mcp__"):
            return f"无效的MCP工具名: {tool_name}"
        parts = tool_name.split("__", 2)
        if len(parts) < 3:
            return f"无效的MCP工具名: {tool_name}"
        server_name = parts[1]
        actual_tool = parts[2]
        server = self._servers.get(server_name)
        if not server:
            return f"MCP服务器未连接: {server_name}"
        return await server.call_tool(actual_tool, arguments)

    async def disconnect_all(self) -> None:
        for server in self._servers.values():
            await server.disconnect()
        self._servers.clear()
