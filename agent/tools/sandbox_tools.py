"""沙箱/运行时工具:读取落盘的超大工具结果、沙箱内执行 Python。

- tool_result_read: 读取 ToolResultDisk 落盘的完整结果(模型对超长工具输出的续读入口)
- python_exec: 在 Python 沙箱子进程中执行代码(超时/隔离)
两者经 ToolContext.extra 注入服务。
"""
from __future__ import annotations

from typing import Any

from .base import Tool, ToolContext


class ToolResultReadTool(Tool):
    name = "tool_result_read"
    description = "读取已被落盘的完整工具结果(结果过长时模型会收到 <tool_result:key> 引用,用它取全文)"
    parameters = {
        "type": "object",
        "properties": {
            "key": {"type": "string", "description": "工具结果引用 key,形如 tool_xxxx_xxx"},
        },
        "required": ["key"],
    }

    async def execute(self, ctx: ToolContext, key: str) -> Any:
        disk = ctx.extra.get("tool_result_disk")
        if disk is None:
            return {"error": "工具结果存储未启用"}
        content = disk.read(str(key).strip())
        if content is None:
            return {"error": f"未找到工具结果: {key}(可能已过期被清理)"}
        return {"content": content}


class PythonExecTool(Tool):
    name = "python_exec"
    description = "在沙箱子进程中执行 Python 代码(超时 30s,输出截断),适合计算/数据处理"
    permission_level = 3  # 代码执行有副作用,普通用户默认 ASK,管理员及以上直接放行
    parameters = {
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "要执行的 Python 代码"},
            "timeout": {"type": "integer", "description": "超时秒数,默认30"},
        },
        "required": ["code"],
    }

    async def execute(self, ctx: ToolContext, code: str, timeout: int = 30) -> Any:
        pool = ctx.extra.get("sandbox_pool")
        if pool is None:
            return {"error": "沙箱会话池未启用"}
        session = pool.get(ctx.event.session_id)
        result = await session.python.run(code, timeout=timeout)
        if result["exit_code"] == 0:
            return {"ok": True, "stdout": result["stdout"][:4000], "exit_code": 0}
        return {"ok": False, "exit_code": result["exit_code"], "stderr": result["stderr"][:1000]}
