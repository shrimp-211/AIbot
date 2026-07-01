"""权限管理系统 — 参考 Claude Code 的 3 层决策模型.

增强功能:
- 基于模式的 allow/deny/ask 权限规则 (如 Bash(rm *), Read(.env), WebFetch(domain:*))
- 规则评估顺序: deny → ask → allow
- 7 级 QQ 角色基础权限
- 后台安全分类器 (可选)
"""

from __future__ import annotations

import fnmatch
import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class PermissionBehavior(Enum):
    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"


@dataclass
class PermissionRule:
    """权限规则."""
    tool_name: str
    rule_content: str | None = None
    behavior: PermissionBehavior = PermissionBehavior.ALLOW
    source: str = "user"

    def matches(self, tool_name: str, tool_input: dict | None = None) -> bool:
        """检查规则是否匹配工具调用."""
        # 工具名匹配
        if not fnmatch.fnmatch(tool_name, self.tool_name):
            return False

        # 无内容规则 → 匹配该工具的所有调用
        if self.rule_content is None:
            return True

        # 内容匹配
        if not tool_input:
            return False

        return self._match_content(tool_input)

    def _match_content(self, tool_input: dict) -> bool:
        """匹配工具调用内容."""
        content = self.rule_content

        # Bash/Shell: 匹配 command
        if self.tool_name in ("bash", "Bash"):
            cmd = tool_input.get("command", "")
            return fnmatch.fnmatch(cmd, content)

        # WebFetch: 匹配 domain
        if self.tool_name in ("web_fetch", "WebFetch"):
            url = tool_input.get("url", "")
            return self._match_domain(url, content)

        # FileRead/Write/Edit: 匹配 file_path
        if self.tool_name in ("file_read", "file_write", "FileRead", "FileWrite", "Edit"):
            path = tool_input.get("path", tool_input.get("file_path", ""))
            return fnmatch.fnmatch(path, content)

        # Agent: 匹配 subagent_type
        if self.tool_name in ("agent", "Agent"):
            agent_type = tool_input.get("subagent_type", "")
            return fnmatch.fnmatch(agent_type, content)

        # 通用: 匹配任何字符串参数
        for val in tool_input.values():
            if isinstance(val, str) and fnmatch.fnmatch(val, content):
                return True

        return False

    @staticmethod
    def _match_domain(url: str, pattern: str) -> bool:
        """域名匹配."""
        from urllib.parse import urlparse
        try:
            hostname = urlparse(url).hostname or ""
            return fnmatch.fnmatch(hostname, pattern)
        except Exception:
            return False


class AuthManager:
    """权限管理器.

    三层决策模型:
    1. 角色等级 (黑名单 → 超级管理员)
    2. 模式规则 (deny → ask → allow)
    3. 安全检查 (注入检测, 危险命令拦截)
    """

    def __init__(self, data_dir: str = "data"):
        self._path = Path(data_dir) / "permissions.json"
        self._data: dict = self._load()

        # 模式权限规则
        self._rules: list[PermissionRule] = []
        self._load_rules()

    def _load(self) -> dict:
        if self._path.exists():
            try:
                return json.loads(self._path.read_text())
            except (json.JSONDecodeError, OSError):
                pass
        return {
            "super_admins": [], "admins": [], "trusted": [],
            "blacklist": [], "rules": [],
        }

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(self._data, ensure_ascii=False, indent=2))

    def _load_rules(self) -> None:
        """加载权限规则."""
        for rule_data in self._data.get("rules", []):
            self._rules.append(PermissionRule(
                tool_name=rule_data.get("tool", "*"),
                rule_content=rule_data.get("content"),
                behavior=PermissionBehavior(rule_data.get("behavior", "allow")),
                source=rule_data.get("source", "config"),
            ))
        # 默认安全规则
        self._add_default_rules()

    def _add_default_rules(self) -> None:
        """添加默认安全规则 (不持久化到文件)."""
        defaults = [
            # 阻止读取敏感文件
            PermissionRule("file_read", "*.env", PermissionBehavior.DENY, "default"),
            PermissionRule("file_read", "*.env.*", PermissionBehavior.DENY, "default"),
            PermissionRule("file_read", "secrets/**", PermissionBehavior.DENY, "default"),
            PermissionRule("file_write", "*.env", PermissionBehavior.DENY, "default"),
            PermissionRule("file_write", "secrets/**", PermissionBehavior.DENY, "default"),
            # 阻止危险命令
            PermissionRule("bash", "rm -rf /*", PermissionBehavior.DENY, "default"),
            PermissionRule("bash", "shutdown *", PermissionBehavior.DENY, "default"),
            PermissionRule("bash", "mkfs.*", PermissionBehavior.DENY, "default"),
            PermissionRule("bash", "> /dev/sda", PermissionBehavior.DENY, "default"),
            PermissionRule("bash", ":(){ :|:& };:", PermissionBehavior.DENY, "default"),
            PermissionRule("bash", "curl *", PermissionBehavior.ASK, "default"),
            PermissionRule("bash", "wget *", PermissionBehavior.ASK, "default"),
            PermissionRule("bash", "sudo *", PermissionBehavior.ASK, "default"),
            # 允许常用安全命令
            PermissionRule("bash", "ls *", PermissionBehavior.ALLOW, "default"),
            PermissionRule("bash", "cat *", PermissionBehavior.ALLOW, "default"),
            PermissionRule("bash", "echo *", PermissionBehavior.ALLOW, "default"),
            PermissionRule("bash", "pwd", PermissionBehavior.ALLOW, "default"),
            PermissionRule("bash", "whoami", PermissionBehavior.ALLOW, "default"),
            PermissionRule("bash", "which *", PermissionBehavior.ALLOW, "default"),
            PermissionRule("bash", "git status", PermissionBehavior.ALLOW, "default"),
            PermissionRule("bash", "git diff *", PermissionBehavior.ALLOW, "default"),
            PermissionRule("bash", "git log *", PermissionBehavior.ALLOW, "default"),
            # 允许读取常见文件
            PermissionRule("file_read", "*.md", PermissionBehavior.ALLOW, "default"),
            PermissionRule("file_read", "*.py", PermissionBehavior.ALLOW, "default"),
            PermissionRule("file_read", "*.json", PermissionBehavior.ALLOW, "default"),
            PermissionRule("file_read", "*.yaml", PermissionBehavior.ALLOW, "default"),
            PermissionRule("file_read", "*.yml", PermissionBehavior.ALLOW, "default"),
            PermissionRule("file_read", "*.toml", PermissionBehavior.ALLOW, "default"),
            PermissionRule("file_read", "*.txt", PermissionBehavior.ALLOW, "default"),
            # 允许搜索
            PermissionRule("web_fetch", "domain:*", PermissionBehavior.ASK, "default"),
        ]
        for rule in defaults:
            if not any(
                r.tool_name == rule.tool_name
                and r.rule_content == rule.rule_content
                and r.behavior == rule.behavior
                for r in self._rules
            ):
                self._rules.append(rule)

    # ---- 权限查询 ----

    def get_level(self, user_id: str) -> int:
        """获取用户权限等级."""
        if user_id in self._data.get("super_admins", []):
            return 7
        if user_id in self._data.get("admins", []):
            return 4
        if user_id in self._data.get("trusted", []):
            return 1
        return 0

    def check_permission(self, user_id: str, required_level: int) -> bool:
        """检查用户是否有足够权限."""
        if self.is_blacklisted(user_id):
            return False
        return self.get_level(user_id) >= required_level

    def check_tool_permission(
        self, user_id: str, tool_name: str, tool_input: dict | None = None, tool_level: int = 0
    ) -> tuple[PermissionBehavior, str]:
        """检查工具调用权限.

        按优先级: deny → ask → allow
        然后检查用户角色等级

        Returns:
            (behavior, reason)
        """
        # 1. 黑名单
        if self.is_blacklisted(user_id):
            return PermissionBehavior.DENY, "用户已被列入黑名单"

        # 2. deny 规则 (最高优先级)
        for rule in self._rules:
            if rule.behavior == PermissionBehavior.DENY and rule.matches(tool_name, tool_input):
                return PermissionBehavior.DENY, f"deny 规则: {rule.tool_name}({rule.rule_content or '*'})"

        # 3. ask 规则
        for rule in self._rules:
            if rule.behavior == PermissionBehavior.ASK and rule.matches(tool_name, tool_input):
                return PermissionBehavior.ASK, f"ask 规则: {rule.tool_name}({rule.rule_content or '*'})"

        # 4. allow 规则
        for rule in self._rules:
            if rule.behavior == PermissionBehavior.ALLOW and rule.matches(tool_name, tool_input):
                # 检查角色等级
                if self.get_level(user_id) >= tool_level:
                    return PermissionBehavior.ALLOW, "allow 规则匹配"
                else:
                    return PermissionBehavior.DENY, f"权限等级不足 (需要 {tool_level}, 当前 {self.get_level(user_id)})"

        # 5. 默认: 检查角色等级，否则 ask
        if self.get_level(user_id) >= tool_level:
            return PermissionBehavior.ASK, "默认行为: 需要用户确认"

        return PermissionBehavior.DENY, f"权限等级不足 (需要 {tool_level}, 当前 {self.get_level(user_id)})"

    def add_rule(self, tool: str, behavior: str, content: str | None = None, source: str = "user") -> None:
        """添加权限规则."""
        rule = PermissionRule(
            tool_name=tool,
            rule_content=content,
            behavior=PermissionBehavior(behavior),
            source=source,
        )
        self._rules.append(rule)
        if source != "default":
            self._data.setdefault("rules", []).append({
                "tool": tool,
                "content": content,
                "behavior": behavior,
                "source": source,
            })
            self._save()

    def remove_rule(self, tool: str, content: str | None = None) -> bool:
        """移除权限规则."""
        for i, rule in enumerate(self._rules):
            if rule.tool_name == tool and rule.rule_content == content:
                self._rules.pop(i)
                self._data["rules"] = [
                    r for r in self._data.get("rules", [])
                    if not (r["tool"] == tool and r.get("content") == content)
                ]
                self._save()
                return True
        return False

    def list_rules(self) -> list[str]:
        """列出所有权限规则."""
        lines = []
        for rule in self._rules:
            content = rule.rule_content or "*"
            lines.append(f"  {rule.behavior.value}: {rule.tool_name}({content}) [{rule.source}]")
        return lines

    # ---- 黑名单 ----

    def is_blacklisted(self, user_id: str) -> bool:
        return user_id in self._data.get("blacklist", [])

    def add_blacklist(self, user_id: str) -> None:
        if user_id not in self._data["blacklist"]:
            self._data["blacklist"].append(user_id)
            self._save()

    def remove_blacklist(self, user_id: str) -> None:
        if user_id in self._data["blacklist"]:
            self._data["blacklist"].remove(user_id)
            self._save()

    # ---- 管理员管理 ----

    def add_admin(self, user_id: str) -> None:
        if user_id not in self._data["admins"]:
            self._data["admins"].append(user_id)
            self._save()

    def remove_admin(self, user_id: str) -> None:
        if user_id in self._data["admins"]:
            self._data["admins"].remove(user_id)
            self._save()

    def add_super_admin(self, user_id: str) -> None:
        if user_id not in self._data["super_admins"]:
            self._data["super_admins"].append(user_id)
            self._save()

    @property
    def super_admins(self) -> list[str]:
        return self._data.get("super_admins", [])

    @property
    def admins(self) -> list[str]:
        return self._data.get("admins", [])
