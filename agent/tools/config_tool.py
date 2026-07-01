"""配置管理工具 — ConfigTool.

参考 Claude Code ConfigTool:
- 让 LLM 可以读取当前配置
- 支持修改指定配置项 (受限)
- 配置修改记录到审计日志
"""

from __future__ import annotations

import copy
from typing import Any

from .base import BaseTool


class ConfigTool(BaseTool):
    name = "config"
    description = "读取和修改当前会话的配置。可以查询配置项或临时修改行为。"
    permission_level = 4  # 需要管理员权限

    def __init__(self, config=None):
        super().__init__()
        self._config = config

    def set_config(self, config) -> None:
        self._config = config

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["get", "set", "list"],
                    "description": "操作: get=读取单个配置, set=临时修改, list=列出所有",
                },
                "key": {
                    "type": "string",
                    "description": "配置键 (仅 get/set 需要), 如 'agent.max_turns'",
                },
                "value": {
                    "type": "string",
                    "description": "新值 (仅 set 需要)",
                },
            },
            "required": ["action"],
        }

    async def execute(self, action: str, key: str = "", value: str = "", **kwargs) -> str:
        if not self._config:
            return "配置系统未初始化"

        if action == "list":
            # 列出主要配置 (隐藏敏感信息)
            sections = ["agent", "provider", "onebot", "security", "memory"]
            lines = ["当前配置:"]
            for section in sections:
                val = self._config.get(section, None)
                if val is None:
                    continue
                if isinstance(val, dict):
                    # 隐藏敏感字段
                    display = copy.deepcopy(val)
                    if "api_key" in display:
                        display["api_key"] = "***"
                    lines.append(f"  [{section}]")
                    for k, v in display.items():
                        if k not in ("api_key", "access_token"):
                            lines.append(f"    {k}: {v}")
                else:
                    lines.append(f"  {section}: {val}")
            return "\n".join(lines)

        elif action == "get":
            if not key:
                return "请指定 key 参数"
            val = self._config.get(key, "__NOT_FOUND__")
            if val == "__NOT_FOUND__":
                return f"配置项不存在: {key}"
            if "api_key" in key or "password" in key or "token" in key:
                val = "***"
            return f"{key} = {val}"

        elif action == "set":
            if not key:
                return "请指定 key 和 value 参数"
            # 只允许修改白名单中的配置
            allowed_keys = {
                "agent.max_turns",
                "agent.system_prompt",
                "agent.rate_limit.window_sec",
                "agent.rate_limit.max_requests",
                "provider.temperature",
                "provider.max_tokens",
            }
            if key not in allowed_keys:
                return f"不允许动态修改配置: {key}。允许修改的配置: {', '.join(sorted(allowed_keys))}"

            # 类型转换
            try:
                if value.isdigit():
                    value = int(value)
                elif value.lower() in ("true", "false"):
                    value = value.lower() == "true"
                elif isinstance(value, str):
                    try:
                        value = float(value)
                    except ValueError:
                        pass
            except Exception:
                pass

            # 通过配置系统设置 (非持久化，仅会话有效)
            self._config.set(key, value)

            from loguru import logger
            logger.info(f"配置已修改: {key} = {value}")
            return f"配置已更新: {key} = {value}"

        return f"未知操作: {action}"
