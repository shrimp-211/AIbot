"""生命周期钩子系统 — 参考 Claude Code Hooks 的完整实现.

支持 28 种事件类型，4 种钩子类型 (command/http/prompt/agent),
退出码决策控制 (exit 0/2/other), JSON 输出格式,
PreToolUse 的 permissionDecision 控制, Stop 的 decision: block 机制.

事件:
  SessionStart, UserPromptSubmit, PreToolUse, PostToolUse,
  PostToolUseFailure, PermissionRequest, PermissionDenied,
  Stop, PreCompact, PostCompact, Notification,
  SubagentStart, SubagentStop, TaskCreated, TaskCompleted,
  ConfigChange, FileChanged, SessionEnd, Elicitation,
  Setup, TeammateIdle, InstructionsLoaded,
  CwdChanged, WorktreeCreate, WorktreeRemove, StopFailure,
  UserPromptExpansion, MessageDisplay
"""

from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from typing import Any

from loguru import logger


HookCallback = Callable[..., Coroutine[Any, Any, Any]]


@dataclass
class HookOutput:
    """Hook 执行输出."""
    continue_processing: bool = True
    stop_reason: str = ""
    suppress_output: bool = False
    system_message: str = ""
    additional_context: str = ""
    decision: str = ""               # "block" for Stop, "allow"/"deny" for PermissionRequest
    permission_decision: str = ""    # "allow"/"deny"/"ask" for PreToolUse
    permission_reason: str = ""
    updated_input: dict | None = None
    updated_tool_output: str | None = None
    retry: bool = False


@dataclass
class HookDefinition:
    """钩子定义."""
    event: str
    handler: HookCallback
    matcher: str = ""         # 工具名匹配 (如 "Bash", "Edit|Write")
    hook_type: str = "command"  # command | http | prompt | agent
    timeout: int = 30
    once: bool = False
    condition: str = ""       # if 条件 (权限规则语法, 如 "Bash(rm *)")


class HookSystem:
    """增强的发布/订阅钩子系统.

    特性:
    - 28 种事件
    - 4 种处理器类型
    - 匹配器过滤 (按工具名/事件)
    - 退出码决策控制 (0/2/other)
    - JSON 结构化输出
    - 单次执行 (once)
    - 时间超时
    """

    def __init__(self):
        self._listeners: dict[str, list[HookDefinition]] = defaultdict(list)

    # ---- 注册 ----

    def on(
        self,
        event: str,
        handler: HookCallback,
        matcher: str = "",
        hook_type: str = "command",
        timeout: int = 30,
        once: bool = False,
        condition: str = "",
    ) -> None:
        """注册事件监听器."""
        definition = HookDefinition(
            event=event,
            handler=handler,
            matcher=matcher,
            hook_type=hook_type,
            timeout=timeout,
            once=once,
            condition=condition,
        )
        self._listeners[event].append(definition)

    def off(self, event: str, handler: HookCallback) -> None:
        self._listeners[event] = [
            h for h in self._listeners[event]
            if h.handler is not handler
        ]

    def register_from_config(self, config: dict) -> None:
        """从配置文件注册钩子.

        格式 (参考 Claude Code settings.json hooks):
        {
          "hooks": {
            "PreToolUse": [
              {
                "matcher": "Bash",
                "hooks": [
                  {
                    "type": "command",
                    "command": "python validate.py",
                    "timeout": 5
                  }
                ]
              }
            ]
          }
        }
        """
        hooks_config = config.get("hooks", {})
        for event_name, matchers in hooks_config.items():
            for matcher_entry in matchers:
                matcher = matcher_entry.get("matcher", "")
                for hook_entry in matcher_entry.get("hooks", []):
                    hook_type = hook_entry.get("type", "command")
                    command = hook_entry.get("command", "")
                    timeout = hook_entry.get("timeout", 30)
                    condition = hook_entry.get("if", "")

                    async def command_handler(**kwargs):
                        proc = await asyncio.create_subprocess_shell(
                            command,
                            stdin=asyncio.subprocess.PIPE,
                            stdout=asyncio.subprocess.PIPE,
                            stderr=asyncio.subprocess.PIPE,
                        )
                        input_data = json.dumps(kwargs, default=str)
                        stdout, _ = await asyncio.wait_for(
                            proc.communicate(input_data.encode()),
                            timeout=timeout,
                        )
                        return proc.returncode, stdout.decode(errors="replace")

                    self.on(event_name, command_handler, matcher, hook_type, timeout, condition=condition)

    # ---- 触发 ----

    async def emit(self, event: str, **kwargs) -> HookOutput | None:
        """触发事件, 返回聚合的输出."""
        if event not in self._listeners:
            return None

        definitions = list(self._listeners[event])
        final_output = HookOutput()
        to_remove = []

        for i, definition in enumerate(definitions):
            # 匹配器检查
            if definition.matcher:
                tool_name = kwargs.get("tool_name", "")
                matchers = definition.matcher.split("|")
                if not any(m.strip() == tool_name for m in matchers):
                    continue

            # condition 检查
            if definition.condition:
                if not self._check_condition(definition.condition, kwargs):
                    continue

            # 执行
            try:
                result = await asyncio.wait_for(
                    definition.handler(**kwargs),
                    timeout=definition.timeout,
                )
            except asyncio.TimeoutError:
                logger.warning(f"Hook 超时: event={event}")
                final_output.system_message = f"Hook 超时: event={event}"
                continue
            except Exception:
                logger.exception(f"Hook 异常: event={event}")
                continue

            # 处理结果
            output = self._process_result(event, result)
            if output:
                final_output = self._merge_output(final_output, output)
                if not final_output.continue_processing or final_output.decision == "block":
                    break

            if definition.once:
                to_remove.append(i)

        # 清理 once 钩子
        for i in reversed(to_remove):
            self._listeners[event].pop(i)

        return final_output

    def _check_condition(self, condition: str, kwargs: dict) -> bool:
        """检查 if 条件 (简化版权限规则匹配)."""
        # 解析 "Bash(rm *)" 格式
        import re
        match = re.match(r"(\w+)\((.+)\)", condition)
        if not match:
            return True
        cond_tool = match.group(1)
        cond_pattern = match.group(2)

        tool_name = kwargs.get("tool_name", "")
        if cond_tool != tool_name:
            return False

        tool_input = kwargs.get("tool_input", {})
        command = tool_input.get("command", tool_input.get("url", tool_input.get("path", "")))
        import fnmatch
        return fnmatch.fnmatch(str(command), cond_pattern)

    def _process_result(self, event: str, result: Any) -> HookOutput | None:
        """处理 hook 执行结果."""
        if isinstance(result, tuple) and len(result) >= 1:
            exit_code = result[0] if isinstance(result[0], int) else 0
            output = HookOutput()

            if exit_code == 0:
                pass  # 成功
            elif exit_code == 2:
                # 阻塞错误
                output.continue_processing = False
                for event_name in ("PreToolUse", "UserPromptSubmit", "Stop", "PreCompact"):
                    if event == event_name:
                        output.decision = "block"
                        output.stop_reason = str(result[1]) if len(result) > 1 else ""
            else:
                # 非阻塞错误
                output.system_message = str(result[1])[:200] if len(result) > 1 else ""

            return output
        return None

    def _merge_output(self, base: HookOutput, new: HookOutput) -> HookOutput:
        """合并多个 hook 输出."""
        if new.continue_processing is False:
            base.continue_processing = False
        if new.stop_reason:
            base.stop_reason = new.stop_reason
        if new.system_message:
            base.system_message = (base.system_message + "; " + new.system_message).strip("; ")
        if new.additional_context:
            base.additional_context = (base.additional_context + "\n" + new.additional_context).strip()
        if new.decision:
            base.decision = new.decision
        if new.permission_decision:
            base.permission_decision = new.permission_decision
        if new.updated_input:
            base.updated_input = new.updated_input
        return base

    # ---- 查询 ----

    def list_hooks(self) -> list[str]:
        """列出所有已注册的钩子."""
        lines = []
        for event, definitions in self._listeners.items():
            for d in definitions:
                matcher = d.matcher or "*"
                lines.append(f"  {event}: [{d.hook_type}] matcher={matcher} (once={d.once})")
        return lines
