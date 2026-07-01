"""复杂任务流程编排引擎 — DAG 规划 + 并行执行 + 检查点 + 熔断.

架构:
  PlanPhase: LLM 分析任务 → 生成 DAG → 预估成本 → 用户确认
  ExecPhase: 拓扑排序 → 并行执行独立节点 → 检查点 → 结果合成
  Monitor:   实时进度 → 耗时追踪 → 成本累计 → 熔断检测

DAG 节点类型:
  - llm_call:  调用 LLM 推理
  - tool_call: 调用工具
  - sub_task:  嵌套子 DAG
  - human:     需要用户输入
  - condition: 条件分支
  - merge:     合并多个上游结果
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from loguru import logger


# ---- 数据模型 ----

class NodeType(Enum):
    LLM_CALL = "llm_call"
    TOOL_CALL = "tool_call"
    SUB_TASK = "sub_task"
    HUMAN = "human"
    CONDITION = "condition"
    MERGE = "merge"


class NodeStatus(Enum):
    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"
    RETRYING = "retrying"


@dataclass
class DAGNode:
    """DAG 中的一个任务节点."""
    id: str
    name: str
    type: NodeType
    description: str = ""
    input_data: dict = field(default_factory=dict)
    output: Any = None
    status: NodeStatus = NodeStatus.PENDING
    depends_on: list[str] = field(default_factory=list)  # 上游节点 ID 列表
    retry_count: int = 0
    max_retries: int = 2
    retry_delay: float = 2.0  # 重试间隔 (秒)
    timeout: float = 120.0  # 超时 (秒)
    started_at: float = 0.0
    finished_at: float = 0.0
    cost: float = 0.0
    tokens_used: int = 0
    error_message: str = ""

    @property
    def duration(self) -> float:
        if self.started_at and self.finished_at:
            return self.finished_at - self.started_at
        return 0.0

    @property
    def is_terminal(self) -> bool:
        return self.status in (NodeStatus.DONE, NodeStatus.FAILED, NodeStatus.SKIPPED)

    def to_dict(self) -> dict:
        return {
            "id": self.id, "name": self.name, "type": self.type.value,
            "status": self.status.value, "depends_on": self.depends_on,
            "duration": round(self.duration, 2), "cost": round(self.cost, 4),
            "tokens": self.tokens_used,
        }


@dataclass
class DAGPlan:
    """完整的 DAG 执行计划."""
    id: str
    goal: str
    nodes: list[DAGNode]
    edges: list[tuple[str, str]]  # (from_id, to_id)
    estimated_cost: float = 0.0
    estimated_time: float = 0.0
    created_at: float = field(default_factory=time.time)
    checkpoint_path: str = ""
    metadata: dict = field(default_factory=dict)

    def get_node(self, node_id: str) -> DAGNode | None:
        for n in self.nodes:
            if n.id == node_id:
                return n
        return None

    def get_ready_nodes(self) -> list[DAGNode]:
        """获取所有就绪节点 (上游全部完成)."""
        ready = []
        for node in self.nodes:
            if node.status != NodeStatus.PENDING:
                continue
            deps_done = all(
                self.get_node(d).status == NodeStatus.DONE
                for d in node.depends_on
            ) if node.depends_on else True
            if deps_done:
                node.status = NodeStatus.READY
                ready.append(node)
        return ready

    def all_done(self) -> bool:
        return all(n.is_terminal for n in self.nodes)

    def progress(self) -> dict:
        total = len(self.nodes)
        done = sum(1 for n in self.nodes if n.status == NodeStatus.DONE)
        failed = sum(1 for n in self.nodes if n.status == NodeStatus.FAILED)
        running = sum(1 for n in self.nodes if n.status == NodeStatus.RUNNING)
        return {"total": total, "done": done, "failed": failed, "running": running,
                "percent": round(done / total * 100, 1) if total else 0}


# ---- 执行引擎 ----

class WorkflowExecutor:
    """DAG 工作流执行引擎.

    特性:
    - 拓扑排序 → 并行执行独立节点
    - 自动重试 (指数退避)
    - 检查点保存/恢复
    - 实时进度回调
    - 熔断: 连续失败 N 次后终止
    - 成本追踪: 累计 token 消耗
    """

    def __init__(self, engine=None, provider=None, tools=None, orchestrator=None):
        self._engine = engine
        self._provider = provider
        self._tools = tools
        self._orchestrator = orchestrator
        self._active_plan: DAGPlan | None = None
        self._progress_callback: callable | None = None
        self._node_executors: dict[NodeType, callable] = {}

        # 熔断配置
        self._circuit_max_failures = 3
        self._circuit_failure_count = 0
        self._circuit_open = False

        # 检查点目录
        self._checkpoint_dir = Path("data/checkpoints")
        self._checkpoint_dir.mkdir(parents=True, exist_ok=True)

        self._register_default_executors()

    def on_progress(self, callback: callable) -> None:
        """注册进度回调."""
        self._progress_callback = callback

    def register_executor(self, node_type: NodeType, executor: callable) -> None:
        self._node_executors[node_type] = executor

    # ---- 规划阶段 ----

    async def plan(self, goal: str, context: dict | None = None) -> DAGPlan:
        """让 LLM 分析任务并生成 DAG 执行计划.

        Args:
            goal: 任务目标
            context: 额外上下文

        Returns:
            生成的 DAGPlan
        """
        if not self._provider:
            raise RuntimeError("WorkflowExecutor 需要 provider 进行规划")

        planning_prompt = (
            "你是一个任务规划专家。请将以下目标分解为可执行的步骤，以 DAG 格式输出。\n\n"
            f"目标: {goal}\n\n"
            "规则:\n"
            "1. 每个步骤有: id, name, type(llm_call|tool_call|sub_task), description, depends_on\n"
            "2. 独立的步骤可以并行执行\n"
            "3. 步骤总数控制在 3-8 个\n"
            "4. 输出纯 JSON 格式:\n"
            '{"nodes": [{"id": "1", "name": "...", "type": "llm_call", '
            '"description": "...", "depends_on": []}, ...]}'
        )

        if context:
            planning_prompt += f"\n上下文:\n{json.dumps(context, ensure_ascii=False, indent=2)}"

        resp = await self._provider.chat(
            messages=[{"role": "user", "content": planning_prompt}],
            tools=None,
        )
        content = resp.get("content", "{}")

        # 解析 JSON
        try:
            # 提取 JSON 块
            import re
            match = re.search(r"\{[\s\S]*\}", content)
            if match:
                data = json.loads(match.group(0))
            else:
                data = json.loads(content)
        except json.JSONDecodeError:
            # Fallback: 创建简单的线性计划
            data = {
                "nodes": [
                    {"id": "1", "name": "分析需求", "type": "llm_call",
                     "description": goal, "depends_on": []},
                    {"id": "2", "name": "执行操作", "type": "tool_call",
                     "description": "执行必要操作", "depends_on": ["1"]},
                    {"id": "3", "name": "整合结果", "type": "llm_call",
                     "description": "整合并输出结果", "depends_on": ["2"]},
                ]
            }

        # 构建 DAG
        plan_id = str(uuid.uuid4())[:8]
        nodes = []
        edges = []

        for node_data in data.get("nodes", []):
            node = DAGNode(
                id=node_data["id"],
                name=node_data["name"],
                type=NodeType(node_data.get("type", "llm_call")),
                description=node_data.get("description", ""),
                depends_on=node_data.get("depends_on", []),
                input_data=node_data.get("input_data", {}),
            )
            nodes.append(node)
            for dep_id in node.depends_on:
                edges.append((dep_id, node.id))

        # 拓扑验证 (检测环)
        if not self._is_dag(nodes, edges):
            logger.warning("LLM 生成的 DAG 包含环，使用线性回退")
            nodes = self._linearize(nodes)

        plan = DAGPlan(
            id=plan_id,
            goal=goal,
            nodes=nodes,
            edges=edges,
            metadata=data.get("metadata", {}),
        )

        self._active_plan = plan
        await self._save_checkpoint(plan)
        logger.info(f"DAG 计划生成: {plan_id} — {len(nodes)} 个节点")
        return plan

    # ---- 执行阶段 ----

    async def execute(self, plan: DAGPlan | None = None) -> dict:
        """执行 DAG 计划.

        Args:
            plan: 要执行的计划 (None = 使用当前活跃计划)

        Returns:
            执行结果汇总
        """
        plan = plan or self._active_plan
        if not plan:
            return {"error": "无执行计划"}

        self._active_plan = plan
        self._circuit_failure_count = 0
        self._circuit_open = False
        start_time = time.time()

        logger.info(f"开始执行 DAG: {plan.id} — {plan.goal[:80]}")

        while not plan.all_done():
            if self._circuit_open:
                logger.error("熔断器已打开，终止执行")
                break

            ready = plan.get_ready_nodes()
            if not ready:
                # 检查是否有节点卡住
                pending = [n for n in plan.nodes if n.status == NodeStatus.PENDING]
                if pending:
                    logger.warning(f"{len(pending)} 个节点因依赖未满足而等待")
                break

            # 并行执行所有就绪节点
            tasks = [self._execute_node(node, plan) for node in ready]
            await asyncio.gather(*tasks, return_exceptions=True)

            # 检查点
            await self._save_checkpoint(plan)

            # 进度回调
            pg = plan.progress()
            if self._progress_callback:
                await self._progress_callback(pg)

        total_time = time.time() - start_time
        total_cost = sum(n.cost for n in plan.nodes)
        total_tokens = sum(n.tokens_used for n in plan.nodes)

        result = {
            "plan_id": plan.id,
            "goal": plan.goal,
            "total_time": round(total_time, 2),
            "total_cost": round(total_cost, 4),
            "total_tokens": total_tokens,
            "progress": plan.progress(),
            "nodes": [n.to_dict() for n in plan.nodes],
            "result": self._synthesize_result(plan),
        }

        logger.info(f"DAG 执行完成: {plan.progress()['percent']}% — {total_time:.1f}s — ${total_cost:.4f}")
        return result

    async def _execute_node(self, node: DAGNode, plan: DAGPlan) -> None:
        """执行单个节点 (含重试和超时)."""
        node.status = NodeStatus.RUNNING
        node.started_at = time.time()

        executor = self._node_executors.get(node.type)
        if not executor:
            node.status = NodeStatus.FAILED
            node.error_message = f"无执行器: {node.type}"
            return

        for attempt in range(node.max_retries + 1):
            try:
                result = await asyncio.wait_for(
                    executor(node, plan),
                    timeout=node.timeout,
                )
                node.output = result
                node.status = NodeStatus.DONE
                node.retry_count = attempt
                self._circuit_failure_count = 0
                break
            except asyncio.TimeoutError:
                node.error_message = f"超时 ({node.timeout}s)"
                if attempt < node.max_retries:
                    node.status = NodeStatus.RETRYING
                    await asyncio.sleep(node.retry_delay * (2 ** attempt))
            except Exception as e:
                node.error_message = str(e)
                if attempt < node.max_retries:
                    node.status = NodeStatus.RETRYING
                    logger.warning(f"节点 {node.id} 失败 (attempt {attempt+1}): {e}")
                    await asyncio.sleep(node.retry_delay * (2 ** attempt))
                else:
                    node.status = NodeStatus.FAILED
                    self._circuit_failure_count += 1
                    logger.error(f"节点 {node.id} 最终失败: {e}")

        node.finished_at = time.time()

        # 熔断检查
        if self._circuit_failure_count >= self._circuit_max_failures:
            self._circuit_open = True

    async def resume(self, plan_id: str) -> DAGPlan | None:
        """从检查点恢复未完成的计划."""
        checkpoint_file = self._checkpoint_dir / f"{plan_id}.json"
        if not checkpoint_file.exists():
            logger.warning(f"检查点不存在: {plan_id}")
            return None

        data = json.loads(checkpoint_file.read_text())
        nodes = [
            DAGNode(
                id=n["id"], name=n["name"], type=NodeType(n["type"]),
                status=NodeStatus(n["status"]), depends_on=n.get("depends_on", []),
                description=n.get("description", ""),
                retry_count=n.get("retry_count", 0),
                output=n.get("output"),
                error_message=n.get("error_message", ""),
            )
            for n in data.get("nodes", [])
        ]
        plan = DAGPlan(
            id=data["id"], goal=data["goal"], nodes=nodes,
            edges=data.get("edges", []), metadata=data.get("metadata", {}),
        )
        self._active_plan = plan
        logger.info(f"已恢复计划: {plan_id} — {plan.progress()['percent']}% 已完成")
        return plan

    # ---- 结果合成 ----

    def _synthesize_result(self, plan: DAGPlan) -> str:
        """合成所有节点的输出."""
        done_nodes = [n for n in plan.nodes if n.status == NodeStatus.DONE]
        if not done_nodes:
            return "无完成节点"

        # 找到最终节点 (没有下游依赖的)
        all_dep_ids = set()
        for n in plan.nodes:
            all_dep_ids.update(n.depends_on)
        final_nodes = [n for n in done_nodes if n.id not in all_dep_ids]

        parts = []
        for node in final_nodes:
            if node.output:
                parts.append(str(node.output)[:2000])
        return "\n\n".join(parts) if parts else str(done_nodes[-1].output)[:2000]

    # ---- DAG 验证 ----

    @staticmethod
    def _is_dag(nodes: list[DAGNode], edges: list[tuple[str, str]]) -> bool:
        """检测 DAG 中是否存在环 (DFS)."""
        adj = defaultdict(list)
        for u, v in edges:
            adj[u].append(v)

        WHITE, GRAY, BLACK = 0, 1, 2
        color = {n.id: WHITE for n in nodes}

        def dfs(u: str) -> bool:
            color[u] = GRAY
            for v in adj[u]:
                if color.get(v, BLACK) == GRAY:
                    return False  # 环
                if color.get(v, BLACK) == WHITE:
                    if not dfs(v):
                        return False
            color[u] = BLACK
            return True

        for node in nodes:
            if color.get(node.id, BLACK) == WHITE:
                if not dfs(node.id):
                    return False
        return True

    @staticmethod
    def _linearize(nodes: list[DAGNode]) -> list[DAGNode]:
        """将有环图转为线性序列."""
        for i, node in enumerate(nodes):
            if i > 0:
                node.depends_on = [nodes[i - 1].id]
            else:
                node.depends_on = []
        return nodes

    # ---- 默认执行器 ----

    def _register_default_executors(self) -> None:
        self._node_executors[NodeType.LLM_CALL] = self._exec_llm_call
        self._node_executors[NodeType.TOOL_CALL] = self._exec_tool_call
        self._node_executors[NodeType.SUB_TASK] = self._exec_sub_task
        self._node_executors[NodeType.MERGE] = self._exec_merge

    async def _exec_llm_call(self, node: DAGNode, plan: DAGPlan) -> str:
        """执行 LLM 调用节点."""
        # 收集上游结果作为上下文
        context = ""
        for dep_id in node.depends_on:
            dep_node = plan.get_node(dep_id)
            if dep_node and dep_node.output:
                context += f"[{dep_node.name} 的结果]\n{str(dep_node.output)[:500]}\n\n"

        prompt = node.input_data.get("prompt", node.description)
        if context:
            prompt = f"上下文:\n{context}\n任务:\n{prompt}"

        # 使用编排器 (如果可用)
        if self._orchestrator:
            resp = await self._orchestrator.orchestrate(
                messages=[{"role": "user", "content": prompt}],
                mode=self._orchestrator.OrchestrationMode.FALLBACK if hasattr(self._orchestrator, 'OrchestrationMode') else None,
            )
            node.cost = resp.cost
            return resp.content

        if self._provider:
            resp = await self._provider.chat([{"role": "user", "content": prompt}])
            return resp.get("content", "")

        return f"[LLM 不可用] 任务: {node.description}"

    async def _exec_tool_call(self, node: DAGNode, plan: DAGPlan) -> str:
        """执行工具调用节点."""
        tool_name = node.input_data.get("tool_name", "")
        tool_args = node.input_data.get("tool_args", {})

        if self._tools:
            result = await self._tools.execute(tool_name, tool_args)
            return str(result)

        return f"[工具不可用] {node.description}"

    async def _exec_sub_task(self, node: DAGNode, plan: DAGPlan) -> str:
        """执行子 DAG (递归)."""
        # 创建子执行器
        sub_executor = WorkflowExecutor(
            engine=self._engine,
            provider=self._provider,
            tools=self._tools,
            orchestrator=self._orchestrator,
        )
        sub_plan = await sub_executor.plan(node.description)
        result = await sub_executor.execute(sub_plan)
        node.tokens_used = result.get("total_tokens", 0)
        return result.get("result", "")

    async def _exec_merge(self, node: DAGNode, plan: DAGPlan) -> str:
        """合并上游节点结果."""
        parts = []
        for dep_id in node.depends_on:
            dep_node = plan.get_node(dep_id)
            if dep_node and dep_node.output:
                parts.append(f"## {dep_node.name}\n{str(dep_node.output)[:1000]}")
        return "\n\n".join(parts)

    # ---- 检查点 ----

    async def _save_checkpoint(self, plan: DAGPlan) -> None:
        data = {
            "id": plan.id, "goal": plan.goal,
            "nodes": [n.to_dict() for n in plan.nodes],
            "edges": list(plan.edges), "metadata": plan.metadata,
        }
        (self._checkpoint_dir / f"{plan.id}.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2)
        )

    # ---- 成本估算 ----

    @staticmethod
    def estimate_cost(node_count: int, avg_tokens_per_node: int = 500) -> dict:
        """预估执行成本."""
        total_tokens = node_count * avg_tokens_per_node
        # 按 GPT-4o 价格估算
        input_cost = total_tokens * 0.0025 / 1000
        output_cost = total_tokens * 0.01 / 1000
        return {
            "estimated_tokens": total_tokens,
            "estimated_cost_usd": round(input_cost + output_cost, 4),
            "node_count": node_count,
        }
