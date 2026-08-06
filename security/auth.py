"""权限与安全:7 级角色 + deny→ask→allow 三层决策 + SSRF 防护。

参考 Claude Code 的权限模型:先匹配 deny 规则(最高优先级),再 ask,
最后 allow;未命中任何规则时默认 allow。
"""
from __future__ import annotations

import fnmatch
import ipaddress
from dataclasses import dataclass, field
from enum import IntEnum
from urllib.parse import urlparse


class Role(IntEnum):
    BLACKLIST = 0
    TRUSTED = 1
    ADMIN = 4
    SUPER_ADMIN = 7


class Decision(IntEnum):
    DENY = -1
    ASK = 0
    ALLOW = 1


@dataclass
class PermissionRule:
    """一条权限规则,按 kind 区分作用对象。"""

    kind: str  # tool | command | domain | path
    target: str
    decision: str  # deny | ask | allow
    level: int = 0  # 要求的最低角色等级

    def matches(self, kind: str, value: str) -> bool:
        if self.kind != kind:
            return False
        value = value.strip()
        if self.kind == "path":
            return value == self.target or value.startswith(self.target.rstrip("/") + "/")
        return fnmatch.fnmatch(value, self.target)


DEFAULT_RULES: list[PermissionRule] = [
    # 敏感文件保护
    PermissionRule("path", ".env", "deny"),
    PermissionRule("path", "secrets", "deny"),
    PermissionRule("path", "src/config.yaml", "deny"),
    # 危险命令直接拒绝
    PermissionRule("command", "rm -rf /*", "deny"),
    PermissionRule("command", "rm -rf ~", "deny"),
    PermissionRule("command", "shutdown*", "deny"),
    PermissionRule("command", "reboot*", "deny"),
    PermissionRule("command", "mkfs*", "deny"),
    PermissionRule("command", "dd if=*of=/dev/*", "deny"),
    PermissionRule("command", "> /dev/sd*", "deny"),
    PermissionRule("command", ":(){ :|:& };:", "deny"),
    PermissionRule("command", "chmod -R 777 /*", "deny"),
    # 敏感命令需 ask
    PermissionRule("command", "curl*", "ask"),
    PermissionRule("command", "wget*", "ask"),
    PermissionRule("command", "sudo*", "ask"),
    PermissionRule("command", "docker rm *", "ask"),
    PermissionRule("command", "git push*", "ask"),
    PermissionRule("command", "pip install*", "ask"),
    PermissionRule("command", "npm install*", "ask"),
]


class AuthManager:
    """角色等级管理 + 权限决策。"""

    def __init__(
        self,
        rules: list[PermissionRule] | None = None,
        admin_users: tuple[str, ...] = (),
        super_admin_users: tuple[str, ...] = (),
    ):
        self.rules: list[PermissionRule] = list(rules or DEFAULT_RULES)
        self.admin_users: set[str] = set(admin_users)
        self.super_admin_users: set[str] = set(super_admin_users)
        self.blacklist: set[str] = set()

    # ---------- 角色 ----------

    def get_role_level(self, user_id: str, group_id: str | None = None) -> int:
        if user_id in self.blacklist:
            return int(Role.BLACKLIST)
        if user_id in self.super_admin_users:
            return int(Role.SUPER_ADMIN)
        if user_id in self.admin_users:
            return int(Role.ADMIN)
        return int(Role.TRUSTED)

    # ---------- 三层决策 ----------

    def _match_rules(self, kind: str, value: str) -> list[PermissionRule]:
        return [r for r in self.rules if r.matches(kind, value)]

    def decide(self, kind: str, value: str) -> Decision:
        """deny → ask → allow 顺序评估,返回决策。"""
        matched = self._match_rules(kind, value)
        if not matched:
            return Decision.ALLOW
        if any(r.decision == "deny" for r in matched):
            return Decision.DENY
        if any(r.decision == "ask" for r in matched):
            return Decision.ASK
        return Decision.ALLOW

    def check_tool(self, tool_name: str, role_level: int, tool_level: int = 0) -> Decision:
        """工具级检查:先看角色等级是否达到工具门槛,再做内容级决策。"""
        if role_level < tool_level:
            return Decision.DENY
        return self.decide("tool", tool_name)

    def check_command(self, command: str, role_level: int) -> Decision:
        return self.decide("command", command)

    def check_path(self, path: str, role_level: int) -> Decision:
        return self.decide("path", path)

    # ---------- 黑名单管理 ----------

    def add_blacklist(self, user_id: str) -> None:
        self.blacklist.add(user_id)

    def remove_blacklist(self, user_id: str) -> None:
        self.blacklist.discard(user_id)

    def is_blacklisted(self, user_id: str) -> bool:
        return user_id in self.blacklist

    # ---------- 序列化 ----------

    def to_dict(self) -> dict:
        return {
            "admin_users": sorted(self.admin_users),
            "super_admin_users": sorted(self.super_admin_users),
            "blacklist": sorted(self.blacklist),
        }

    def load_dict(self, data: dict) -> None:
        self.admin_users = set(data.get("admin_users", []))
        self.super_admin_users = set(data.get("super_admin_users", []))
        self.blacklist = set(data.get("blacklist", []))


# ---------- SSRF 防护 ----------

_PRIVATE_NETWORKS = (
    "10.0.0.0/8",
    "172.16.0.0/12",
    "192.168.0.0/16",
    "127.0.0.0/8",
    "169.254.0.0/16",
    "::1",
    "fc00::/7",
    "fe80::/10",
)


def is_private_hostname(hostname: str) -> bool:
    """判断主机名/IP 是否指向内网地址(SSRF 防御)。"""
    if not hostname:
        return True
    hostname = hostname.lower().rstrip(".")
    if hostname in ("localhost", "localhost.localdomain"):
        return True
    try:
        ip = ipaddress.ip_address(hostname)
    except ValueError:
        return False
    return any(ip in ipaddress.ip_network(net) for net in _PRIVATE_NETWORKS)


def is_safe_url(url: str) -> bool:
    """检查 URL 是否安全(协议白名单 + 非内网)。"""
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    return not is_private_hostname(parsed.hostname or "")
