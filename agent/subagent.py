"""子代理委派系统(参考 Claude Code Subagent / Hermes subagent)。

主代理可将独立任务(调研/规划/执行)委派给子代理。子代理运行在独立的
消息上下文中,拥有自己的 ReAct 循环,结果以文本返回主代理,不污染
主对话上下文。支持并行提交与结果轮询。

子代理类型:
- explore:只读调研(搜索/抓取/读文件/查记忆)
- plan:只读 + 任务规划
- general:使用全量工具执行
"""
from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any, Awaitable, Callable

from loguru import logger

ToolExecutor = Callable[[str, dict, list[str]], Awaitable[str]]

_READONLY_TOOLS = [
    "web_search",
    "web_fetch",
    "file_read",
    "glob",
    "grep",
    "memory_search",
    "knowledge_search",
    "task",
]

SUBAGENT_TYPES: dict[str, dict[str, Any]] = {
    "explore": {
        "system": (
            "你是只读调研子代理,负责快速收集信息并给出结构化结论。"
            "优先用搜索与读文件工具,不要执行写操作或命令。"
            "完成后给出结论、依据与不确定之处。"
        ),
        "tools": _READONLY_TOOLS,
    },
    "plan": {
        "system": (
            "你是规划子代理,负责把复杂任务拆解为可执行步骤(用 task 工具记录),"
            "并只读调研必要信息。不要执行有副作用的操作。"
        ),
        "tools": _READONLY_TOOLS,
    },
    "general": {
        "system": (
            "你是通用执行子代理,负责完成委派的具体任务。"
            "可使用全部工具,完成后汇报结果与关键步骤。"
        ),
        "tools": None,  # 无白名单 = 全部
    },
}


class Subagent:
    """独立上下文运行的子代理 ReAct 循环。"""

    def __init__(
        self,
        provider: Any,
        system_prompt: str,
        tools_schema: list[dict],
        executor: ToolExecutor,
        max_iterations: int = 5,
    ):
        self._provider = provider
        self._system_prompt = system_prompt
        self._tools_schema = tools_schema
        self._executor = executor
        self._max_iterations = max_iterations

    async def run(self, prompt: str) -> str:
        messages: list[dict] = [{"role": "user", "content": prompt}]
        for _ in range(self._max_iterations):
            result = await self._provider.chat(
                messages, system_prompt=self._system_prompt, tools=self._tools_schema
            )
            content = (result.get("content") or "").strip()
            tool_calls = result.get("tool_calls") or []
            if not tool_calls:
                return content if content else "完成。"

            messages.append(
                {
                    "role": "assistant",
                    "content": content or None,
                    "tool_calls": [
                        {
                            "id": tc["id"],
                            "type": "function",
                            "function": {
                                "name": tc["name"],
                                "arguments": json.dumps(
                                    tc.get("arguments", {}), ensure_ascii=False
                                ),
                            },
                        }
                        for tc in tool_calls
                    ],
                }
            )
            for tc in tool_calls:
                name = tc.get("name", "")
                args = tc.get("arguments", {}) or {}
                if not isinstance(args, dict):
                    args = {}
                try:
                    output = await self._executor(name, args, self._tool_filter)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    logger.exception("子代理工具 {} 执行异常", name)
                    output = f"工具执行错误: {type(exc).__name__}: {exc}"
                messages.append({"role": "tool", "tool_call_id": tc["id"], "content": output[:4000]})
        return "子代理已达到迭代上限,基于已获得的信息给出结论。"

    @property
    def _tool_filter(self) -> list[str] | None:
        return None  # 白名单已在构建 schema 时生效


class SubagentManager:
    """子代理调度器:提交/查询/并行执行。"""

    def __init__(self, provider: Any, config: Any):
        self._provider = provider
        self._config = config
        self._executor: ToolExecutor | None = None
        self._jobs: dict[str, asyncio.Task] = {}
        self._results: dict[str, dict[str, Any]] = {}
        self._history: list[dict] = []

    def bind_executor(self, executor: ToolExecutor) -> None:
        self._executor = executor

    def submit(
        self,
        prompt: str,
        agent_type: str = "explore",
        tool_schemas: list[dict] | None = None,
        max_iterations: int | None = None,
    ) -> str:
        """提交一个子代理任务,立即返回 job_id。"""
        agent_type = agent_type if agent_type in SUBAGENT_TYPES else "explore"
        job_id = uuid.uuid4().hex[:10]
        task = asyncio.get_running_loop().create_task(
            self._run(job_id, agent_type, prompt, tool_schemas, max_iterations)
        )
        self._jobs[job_id] = task
        return job_id

    async def _run(
        self,
        job_id: str,
        agent_type: str,
        prompt: str,
        tool_schemas: list[dict] | None,
        max_iterations: int | None,
    ) -> None:
        try:
            spec = SUBAGENT_TYPES[agent_type]
            schemas = tool_schemas or []
            if spec.get("tools"):
                allow = set(spec["tools"])
                schemas = [s for s in schemas if s["name"] in allow]
            sub = Subagent(
                provider=self._provider,
                system_prompt=spec["system"],
                tools_schema=schemas,
                executor=self._executor,
                max_iterations=max_iterations or max(2, int(self._config.get("agent.max_iterations", 8) or 8) // 2),
            )
            result = await sub.run(prompt)
            self._results[job_id] = {"status": "done", "result": result}
        except asyncio.CancelledError:
            self._results[job_id] = {"status": "cancelled"}
        except Exception as exc:  # noqa: BLE001
            logger.exception("子代理 {} 执行异常", job_id)
            self._results[job_id] = {"status": "error", "error": str(exc)}
        finally:
            self._jobs.pop(job_id, None)
            status = (self._results.get(job_id) or {}).get("status", "unknown")
            self._record(job_id, status)

    async def get_result(self, job_id: str, timeout: float = 120) -> dict[str, Any]:
        """查询结果;任务未完成则等待至 timeout。"""
        if job_id in self._results:
            return self._results[job_id]
        job = self._jobs.get(job_id)
        if job is None:
            return {"status": "not_found"}
        try:
            await asyncio.wait_for(asyncio.shield(job), timeout=timeout)
        except asyncio.TimeoutError:
            return {"status": "running"}
        return self._results.get(job_id, {"status": "error", "error": "未知状态"})

    def running(self) -> list[str]:
        return list(self._jobs.keys())

    def recent(self, limit: int = 10) -> list[dict]:
        return self._history[-limit:]

    def _record(self, job_id: str, status: str) -> None:
        self._history.append({"job_id": job_id, "status": status})
        self._history = self._history[-50:]


def build_tool_schemas(tool_registry: Any) -> list[dict]:
    """从 ToolRegistry 提取工具 schema 列表。"""
    if tool_registry is None:
        return []
    try:
        return tool_registry.schemas()
    except Exception:  # noqa: BLE001
        return []
