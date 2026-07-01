"""Agent 引擎 — ReAct 循环 (Think → Act → Observe).

参考 Claude Code 的 Tool Loop 架构:
1. 构建 System Prompt (工具描述 + 记忆 + 人格 + 规则)
2. 调用 LLM，获取思考结果
3. 如果 LLM 请求工具调用 → 执行工具 → 将结果回传 → 回到步骤 2
4. 如果 LLM 返回最终回复 → 输出结果
5. 反思评估 + 记忆保存
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from loguru import logger

from .memory.store import MemoryStore
from .tools.base import BaseTool
from .tools.registry import ToolRegistry
from .skills.registry import SkillRegistry
from .hooks import HookSystem


class AgentEngine:
    """Agent 引擎 — 管理 LLM 与工具的交互循环.

    核心方法是 process()，接收用户消息后运行完整的 ReAct 循环。
    """

    def __init__(
        self,
        provider,              # Provider 实例 (providers/base.py)
        tools: ToolRegistry,
        skills: SkillRegistry,
        memory: MemoryStore,
        auth,                  # AuthManager 实例
        system_prompt: str = "",
        max_turns: int = 15,
        compressor_enabled: bool = True,
        max_context_tokens: int = 8000,
    ):
        self.provider = provider
        self.tools = tools
        self.skills = skills
        self.memory = memory
        self.auth = auth
        self.base_system_prompt = system_prompt
        self.max_turns = max_turns

        from .compressor import ContextCompressor
        self.compressor = ContextCompressor(
            max_tokens=max_context_tokens,
            trigger_ratio=0.82,
            keep_recent=5,
        ) if compressor_enabled else None

        self.hooks = HookSystem()
        self.orchestrator = None
        self._plan_mode = False
        self._cached_system_prompt: str = ""  # 缓存工具描述等静态部分
        self._cached_prompt_key: int = 0
        self.trace_callback: callable | None = None

    # ---- System Prompt 构建 ----

    def _build_system_prompt(self, session_id: str, user_id: str) -> str:
        """构建完整的 System Prompt (静态部分缓存)."""
        tool_count = len(self.tools)

        # 静态部分: 人格 + 工具 + 规则 (缓存)
        if self._cached_prompt_key != tool_count or not self._cached_system_prompt:
            static = []
            if self.base_system_prompt:
                static.append(self.base_system_prompt)
            tool_descs = self.tools.get_descriptions()
            if tool_descs:
                static.append("\n## 可用工具\n")
                for t in tool_descs:
                    static.append(f"- **{t['name']}**: {t['description']}")
            static.append("\n## 规则\n"
                "1. 使用工具前评估必要性，优先使用已有知识\n"
                "2. 回复简洁清晰，不超过 500 字\n"
                "3. STOP=停止, HELP=帮助, STATUS=状态\n"
                "4. 执行危险操作前需要确认")
            self._cached_system_prompt = "\n".join(static)
            self._cached_prompt_key = tool_count

        parts = [self._cached_system_prompt]

        # 动态部分
        now = datetime.now().strftime("%Y年%m月%d日 %H:%M")
        parts.append(f"\n当前时间: {now}")

        recent = self.memory.get_episodic(session_id, limit=5)
        if recent:
            parts.append("\n## 近期对话\n")
            for entry in recent:
                role = "用户" if entry["role"] == "user" else "助手"
                parts.append(f"{role}: {entry['content'][:200]}")

        profile = self.memory.get_user_profile(user_id)
        if profile:
            parts.append(f"\n## 用户信息\n{profile}")

        return "\n".join(parts)

    # ---- 消息构建 ----

    def _build_messages(
        self,
        session_id: str,
        user_id: str,
        user_message: str,
    ) -> list[dict]:
        """构建发送给 LLM 的消息列表."""
        messages = [
            {"role": "system", "content": self._build_system_prompt(session_id, user_id)},
        ]

        # 工作记忆 (最近 N 条对话)
        working = self.memory.get_working(session_id)
        for entry in working:
            messages.append({"role": entry["role"], "content": entry["content"]})

        # 当前用户消息
        messages.append({"role": "user", "content": user_message})

        return messages

    # ---- 核心处理 ----

    async def process(
        self,
        message: str,
        session_id: str,
        user_id: str,
        user_name: str = "",
    ) -> str:
        """处理用户消息并返回回复.

        Args:
            message: 用户消息文本
            session_id: 会话标识符
            user_id: 用户 QQ 号
            user_name: 用户昵称 (可选)

        Returns:
            Agent 的回复文本
        """
        # Hook: before_process
        await self.hooks.emit("before_process", message=message, session_id=session_id)

        # NLU 快速路径
        nlu_result = self._nlu_quick_check(message)
        if nlu_result:
            await self.hooks.emit("after_process", result=nlu_result, session_id=session_id)
            return nlu_result

        # 保存用户消息到工作记忆
        self.memory.add_working(session_id, "user", f"{user_name}: {message}" if user_name else message)

        # 构建消息
        messages = self._build_messages(session_id, user_id, message)

        # 上下文压缩检查
        if self.compressor:
            messages = await self.compressor.compress(messages, self.provider, strategy="auto")

        # ReAct 循环 (共享实现)
        result = await self._run_react_loop(
            messages=messages,
            tools=self.tools,
            max_turns=self.max_turns,
            user_id=user_id,
            tool_result_max_len=8000,
        )

        # 保存回复到工作记忆
        self.memory.add_working(session_id, "assistant", result)

        # 保存到短期记忆
        self.memory.add_episodic(session_id, "user", message)
        self.memory.add_episodic(session_id, "assistant", result)

        # Hook: after_process
        await self.hooks.emit("after_process", result=result, session_id=session_id)

        return result

    # ---- 子代理 ----

    async def run_sub_agent(
        self,
        prompt: str,
        system_prompt: str = "",
        tools: list | None = None,
        max_turns: int = 10,
    ) -> str:
        """在隔离上下文中执行子代理任务.

        Args:
            prompt: 子代理的任务描述
            system_prompt: 自定义系统提示词
            tools: 工具列表 (None = 只读工具)
            max_turns: 最大轮次

        Returns:
            子代理的执行结果
        """
        from .tools.registry import ToolRegistry

        # 构建子工具注册表
        sub_tools = ToolRegistry()
        if tools:
            for t in tools:
                sub_tools.register(t)

        tool_defs = sub_tools.get_openai_tools()

        # 子代理的简化 system prompt
        sub_system = (
            "你是一个专注于特定子任务的 AI 助手。\n"
            f"{system_prompt}\n\n"
            "指令:\n"
            "1. 专注于完成分配给你的具体任务\n"
            "2. 使用可用工具完成任务\n"
            "3. 完成后输出清晰的结果摘要\n"
            "4. 回复简洁，直接给出结果\n"
        )

        messages = [
            {"role": "system", "content": sub_system},
            {"role": "user", "content": prompt},
        ]

        logger.debug(f"子代理启动 (max_turns={max_turns})")
        return await self._run_react_loop(
            messages=messages,
            tools=sub_tools,
            max_turns=max_turns,
            user_id="sub_agent",
            tool_result_max_len=4000,
        )

    # ---- 共享 ReAct 循环 ----

    async def _run_react_loop(
        self,
        messages: list[dict],
        tools,  # ToolRegistry
        max_turns: int,
        user_id: str = "unknown",
        tool_result_max_len: int = 8000,
    ) -> str:
        """共享的 ReAct 循环 — process() 和 run_sub_agent() 共用.

        Args:
            messages: 初始消息列表 (会被原地修改)
            tools: 工具注册表
            max_turns: 最大循环轮次
            user_id: 用于权限检查
            tool_result_max_len: 工具结果截断长度

        Returns:
            最终文本回复
        """
        tool_defs = tools.get_openai_tools() if hasattr(tools, 'get_openai_tools') else None
        result = None

        for turn in range(max_turns):
            await self.hooks.emit("on_llm_request", messages=messages, tools=tool_defs, turn=turn)

            # 使用编排器 (如果可用) 否则直接调用 provider
            if self.orchestrator and len(self.orchestrator._providers) > 1:
                from agent.orchestrator import OrchestrationMode
                response = await self.orchestrator.orchestrate(
                    messages=messages,
                    mode=OrchestrationMode.FALLBACK,
                    tools=tool_defs,
                )
                # Convert ModelResponse to dict format
                response = {"content": response.content, "tool_calls": None}
            else:
                try:
                    response = await self.provider.chat(
                        messages=messages,
                        tools=tool_defs,
                    )
                except Exception:
                    logger.exception(f"LLM 调用失败 (turn {turn})")
                    return "抱歉，AI 服务暂时不可用，请稍后再试。"

            await self.hooks.emit("on_llm_response", response=response, turn=turn)

            if response.get("tool_calls"):
                # Plan模式: 阻止写操作
                if self._plan_mode:
                    for tc in response["tool_calls"]:
                        tool_name = tc["function"]["name"]
                        if tool_name in ("file_write", "bash", "task_create", "task_update",
                                         "config", "knowledge_add", "qq_kick", "qq_mute",
                                         "qq_set_admin", "qq_recall", "qq_essence"):
                            return "Plan模式: 只能读取和探索, 不能执行写操作。退出Plan模式后可以实施。"

                for tc in response["tool_calls"]:
                    tool_name = tc["function"]["name"]
                    tool_args_str = tc["function"].get("arguments", "{}")
                    try:
                        tool_args = json.loads(tool_args_str)
                    except json.JSONDecodeError:
                        tool_result = f"参数解析失败: {tool_args_str}"
                    else:
                        await self.hooks.emit("before_tool_exec", tool_name=tool_name, args=tool_args)
                        tool_result = await tools.execute(
                            tool_name, tool_args, user_id=user_id, auth=self.auth,
                        )
                        await self.hooks.emit("after_tool_exec", tool_name=tool_name, result=tool_result)
                        if self.trace_callback:
                            self.trace_callback(tool_name, tool_args, str(tool_result)[:200])

                    messages.append({
                        "role": "assistant", "content": None,
                        "tool_calls": [{"id": tc.get("id", f"call_{turn}"), "type": "function",
                                        "function": {"name": tool_name, "arguments": tool_args_str}}],
                    })
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.get("id", f"call_{turn}"),
                        "content": str(tool_result)[:tool_result_max_len],
                    })
                continue

            result = response.get("content", "")
            if not result:
                result = "我暂时无法回答这个问题。"
            break

        if result is None:
            result = "抱歉，这个任务太复杂了，我暂时无法完成。请尝试简化你的问题。"
        return result

    # ---- Plan Mode ----

    def enter_plan_mode(self) -> None:
        """进入Plan模式 — 只读探索, 不编辑文件."""
        self._plan_mode = True
        logger.info("进入 Plan 模式 (只读)")

    def exit_plan_mode(self) -> None:
        """退出Plan模式."""
        self._plan_mode = False
        logger.info("退出 Plan 模式")

    @property
    def is_plan_mode(self) -> bool:
        return self._plan_mode

    # ---- NLU 快速路径 ----

    def _nlu_quick_check(self, message: str) -> str | None:
        """自然语言理解快速检查 — 无需调用 LLM 即可响应."""
        msg = message.strip().upper()

        if msg == "STOP":
            return "已停止当前操作。有什么我可以帮你的吗？"

        if msg == "HELP":
            return self._help_text()

        if msg == "STATUS":
            memory_count = self.memory.get_working_count()
            return (
                f"QQ AI Agent 运行正常\n"
                f"- 当前会话消息数: {memory_count}\n"
                f"- 可用工具: {len(self.tools)} 个\n"
                f"- 输入 HELP 查看更多"
            )

        return None

    def _help_text(self) -> str:
        """生成帮助文本."""
        tools_list = "\n".join(
            f"  • {t['name']} — {t['description'][:60]}"
            for t in self.tools.get_descriptions()
        )
        return (
            "万能 QQ AI Agent 使用说明:\n\n"
            "💬 直接发送消息与我对话\n"
            "🔧 我会自动使用工具帮你完成任务\n\n"
            "快速命令:\n"
            "  STOP — 停止当前操作\n"
            "  HELP — 显示此帮助\n"
            "  STATUS — 查看状态\n\n"
            f"可用工具 ({len(self.tools)} 个):\n"
            f"{tools_list}"
        )
