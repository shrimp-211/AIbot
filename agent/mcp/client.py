"""MCP(Model Context Protocol)客户端:stdio 服务器管理与工具发现。

通过 stdio 子进程启动 MCP 服务器(如 `npx @modelcontextprotocol/server-github`),
使用 JSON-RPC 2.0 完成初始化握手、tools/list 工具发现与 tools/call 调用。

协议要点:
- 每个 JSON-RPC 消息一行(stdin 写请求 / stdout 读响应)
- 以 `#` 开头的行是注释,忽略
- initialize → notifications/initialized → tools/list
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

logger = logging.getLogger("mcp")

_MCP_PROTOCOL_VERSION = "2024-11-05"
_JSONRPC_TIMEOUT = 30


class MCPServer:
    """单个 MCP 服务器连接(stdio 子进程)。"""

    def __init__(
        self,
        name: str,
        command: str,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
        permission_level: int = 1,
    ):
        self.name = name
        self.command = command
        self.args = args or []
        self.env = env or {}
        self.permission_level = permission_level

        self._proc: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task | None = None
        self._pending: dict[str, asyncio.Future] = {}
        self._seq = 0
        self._tools: list[dict[str, Any]] = []
        self._server_info: dict[str, Any] = {}
        self._log_lines: list[str] = []

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    @property
    def tools(self) -> list[dict[str, Any]]:
        return list(self._tools)

    @property
    def server_info(self) -> dict[str, Any]:
        return dict(self._server_info)

    async def start(self) -> None:
        """启动进程并完成 MCP 握手、工具发现。"""
        if self.running:
            return
        self._proc = await asyncio.create_subprocess_exec(
            self.command,
            *self.args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**os.environ, **self.env},
        )
        if self._proc.stdin is None or self._proc.stdout is None:
            raise RuntimeError(f"MCP 服务器 {self.name} 无法建立 IO 管道")
        self._reader_task = asyncio.get_running_loop().create_task(self._read_loop())

        # 握手
        self._server_info = await self._request(
            "initialize",
            {
                "protocolVersion": _MCP_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "qq-ai-agent", "version": "1.0.0"},
            },
        )
        self._notify("notifications/initialized", {})

        # 工具发现
        result = await self._request("tools/list", {})
        self._tools = result.get("tools", []) or []
        logger.info(
            "MCP 服务器 %s 已连接(protocol=%s, 工具 %d 个)",
            self.name,
            self._server_info.get("protocolVersion", "?"),
            len(self._tools),
        )

    async def _read_loop(self) -> None:
        assert self._proc is not None and self._proc.stdout is not None
        while True:
            raw = await self._proc.stdout.readline()
            if not raw:
                break
            line = raw.decode("utf-8", "ignore").strip()
            if not line or line.startswith("#"):
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                logger.warning("MCP %s 收到非法 JSON: %.200s", self.name, line)
                continue
            if "id" in msg:
                fut = self._pending.pop(str(msg["id"]), None)
                if fut is not None and not fut.done():
                    fut.set_result(msg)
            elif msg.get("method") == "notifications/message":
                params = msg.get("params", {})
                logger.info("MCP %s 日志[%s]: %s", self.name, params.get("level"), params.get("message", ""))
                self._log_lines.append(str(params.get("message", "")))

    def _notify(self, method: str, params: dict[str, Any]) -> None:
        self._write({"jsonrpc": "2.0", "method": method, "params": params})

    def _write(self, msg: dict[str, Any]) -> None:
        assert self._proc is not None and self._proc.stdin is not None
        self._proc.stdin.write((json.dumps(msg) + "\n").encode("utf-8"))

    async def _drain(self) -> None:
        assert self._proc is not None and self._proc.stdin is not None
        await self._proc.stdin.drain()

    async def _request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self._seq += 1
        req_id = self._seq
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[str(req_id)] = fut
        self._write({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params})
        await self._drain()
        try:
            resp = await asyncio.wait_for(fut, timeout=_JSONRPC_TIMEOUT)
        except asyncio.TimeoutError:
            self._pending.pop(str(req_id), None)
            raise TimeoutError(f"MCP 请求超时: {self.name}::{method}")
        if "error" in resp:
            err = resp["error"]
            raise RuntimeError(f"MCP 错误: {self.name}::{method} code={err.get('code')} msg={err.get('message')}")
        return resp.get("result", {})

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        """调用 MCP 工具,返回提取的文本内容。"""
        result = await self._request("tools/call", {"name": name, "arguments": arguments or {}})
        content = result.get("content", []) or []
        texts = [c.get("text", "") for c in content if c.get("type") == "text"]
        if texts:
            return "\n".join(texts)
        return json.dumps(result, ensure_ascii=False)[:4000]

    async def stop(self) -> None:
        """优雅关闭:shutdown + exit,超时则强杀。"""
        try:
            await asyncio.wait_for(self._request("shutdown", {}), timeout=5)
            self._notify("notifications/exit", {})
            await self._drain()
        except Exception:  # noqa: BLE001
            pass
        if self._proc is not None:
            self._proc.terminate()
            try:
                await asyncio.wait_for(self._proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                self._proc.kill()
        if self._reader_task is not None:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass
            self._reader_task = None
        self._proc = None
        logger.info("MCP 服务器 %s 已关闭", self.name)

    def status(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "running": self.running,
            "command": f"{self.command} {' '.join(self.args)}",
            "tools": [t.get("name") for t in self._tools],
            "protocol": self._server_info.get("protocolVersion", ""),
        }


class MCPManager:
    """管理多个 MCP 服务器连接。"""

    def __init__(self) -> None:
        self._servers: dict[str, MCPServer] = {}

    async def start_servers(self, servers_config: list[dict] | None) -> None:
        """按配置启动所有 MCP 服务器(配置项: name/command/args/env/permission_level)。"""
        for cfg in servers_config or []:
            name = cfg.get("name", "")
            if not name or name in self._servers:
                continue
            server = MCPServer(
                name=name,
                command=cfg.get("command", ""),
                args=list(cfg.get("args", []) or []),
                env=dict(cfg.get("env", {}) or {}),
                permission_level=int(cfg.get("permission_level", 1) or 1),
            )
            try:
                await server.start()
            except Exception:  # noqa: BLE001
                logger.exception("MCP 服务器 %s 启动失败", name)
                continue
            self._servers[name] = server

    def get_server(self, name: str) -> MCPServer | None:
        return self._servers.get(name)

    def list_servers(self) -> list[MCPServer]:
        return list(self._servers.values())

    def list_status(self) -> list[dict[str, Any]]:
        return [s.status() for s in self._servers.values()]

    async def stop_all(self) -> None:
        for server in self._servers.values():
            try:
                await server.stop()
            except Exception:  # noqa: BLE001
                logger.exception("MCP 服务器 %s 关闭异常", server.name)
        self._servers.clear()
