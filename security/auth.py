"""权限与安全:7 级角色 + deny→ask→allow 三层决策 + SSRF 防护。

参考 Claude Code 的权限模型:先匹配 deny 规则(最高优先级),再 ask,
最后 allow;未命中任何规则时默认 allow。
"""
from __future__ import annotations

import asyncio
import fnmatch
import ipaddress
import os
import re
import socket
import time
import uuid
from dataclasses import dataclass, field
from enum import IntEnum
from pathlib import Path
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
            return self._match_path(value)
        return fnmatch.fnmatch(value, self.target)

    def _match_path(self, value: str) -> bool:
        """路径规则匹配:规范化后按文件名/目录前缀判定。

        目标为单段(如 `.env`、`secrets`)→ 匹配规范化路径中任意同名段,
        规避 `./.env`、`data/.env` 等前缀写法绕过;多段目标(如
        `src/config.yaml`)→ 按绝对路径前缀匹配。
        """
        if self.target == "/":
            return True
        target_parts = [p for p in Path(self.target).parts if p not in ("", ".", os.sep)]
        if not target_parts:
            return False
        try:
            norm_parts = list(Path(value).expanduser().resolve().parts)
        except OSError:
            norm_parts = list(Path(value).expanduser().parts)
        if len(target_parts) == 1:
            name = target_parts[0]
            return name in norm_parts
        # 多段:要求 value 解析后含完整目标路径片段(顺序一致)
        target_lower = [p.lower() for p in target_parts]
        norm_lower = [p.lower() for p in norm_parts]
        for i in range(len(norm_lower) - len(target_lower) + 1):
            if norm_lower[i : i + len(target_lower)] == target_lower:
                return True
        return False


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
        trusted_folders: list[str] | None = None,
        sandbox_enabled: bool = False,
    ):
        self.rules: list[PermissionRule] = list(rules or DEFAULT_RULES)
        self.admin_users: set[str] = set(admin_users)
        self.super_admin_users: set[str] = set(super_admin_users)
        self.blacklist: set[str] = set()
        self.trusted_folders: list[Path] = [Path(f).resolve() for f in (trusted_folders or [])]
        self.sandbox_enabled = sandbox_enabled
        # 配对审批(OpenClaw pairing):已批准使用 bot 的用户
        self._approved_users: set[str] = set()
        self.pending_approvals: dict[str, dict] = {}
        self._approval_ttl = 600  # 审批码有效期(秒)
        # 工具级交互审批(Claude Code 权限批准):user_id -> 待审批的工具请求
        self.pending_tool_approvals: dict[str, dict] = {}
        # 临时工具授权(user_id, tool) -> {"ts", "ttl"}:批准后短时放行
        self._temp_tool_allows: dict[tuple[str, str], dict] = {}
        self._tool_approval_ttl = 300  # 工具审批请求有效期(秒)

    # ---------- 角色 ----------

    def get_role_level(self, user_id: str, group_id: str | None = None) -> int:
        if user_id in self.blacklist:
            return int(Role.BLACKLIST)
        if user_id in self.super_admin_users:
            return int(Role.SUPER_ADMIN)
        if user_id in self.admin_users:
            return int(Role.ADMIN)
        return int(Role.TRUSTED)

    def is_admin_or_super(self, user_id: str) -> bool:
        return user_id in self.admin_users or user_id in self.super_admin_users

    # ---------- 可信目录 / 沙箱 ----------

    def is_path_trusted(self, path: str) -> bool:
        """检查路径是否位于可信目录内(空列表 = 全部路径可信)。"""
        if not self.trusted_folders:
            return True
        resolved = Path(path).resolve()
        return any(str(resolved).startswith(str(f)) for f in self.trusted_folders)

    # ---------- 配对审批(OpenClaw pairing) ----------

    def request_pairing(self, user_id: str, group_id: str | None = None) -> str:
        """为未配对用户生成审批码,返回审批码。

        已有未过期审批码则复用(单用户最多 1 个待审批码,天然限流,
        避免刷码占用 pending 表)。
        """
        self._expire_approvals()
        for code, r in self.pending_approvals.items():
            if r["user_id"] == user_id:
                return code
        code = uuid.uuid4().hex[:6].upper()
        self.pending_approvals[code] = {
            "user_id": user_id,
            "group_id": group_id,
            "ts": time.time(),
        }
        return code

    def approve_pairing(self, code: str, approver_id: str) -> dict:
        """管理员批准配对码,授权该用户使用 bot。"""
        self._expire_approvals()
        record = self.pending_approvals.pop(code.upper(), None)
        if not record:
            return {"error": f"审批码不存在或已过期: {code}"}
        self._approved_users.add(record["user_id"])
        return {"ok": True, "user_id": record["user_id"], "approved_by": approver_id}

    def is_paired(self, user_id: str) -> bool:
        return user_id in self._approved_users

    def has_pending(self, user_id: str) -> bool:
        self._expire_approvals()
        return any(r["user_id"] == user_id for r in self.pending_approvals.values())

    def _expire_approvals(self) -> None:
        now = time.time()
        expired = [c for c, r in self.pending_approvals.items() if now - r.get("ts", 0) > self._approval_ttl]
        for c in expired:
            self.pending_approvals.pop(c, None)

    # ---------- 工具级交互审批(Claude Code 权限批准) ----------

    def request_tool_approval(
        self, user_id: str, tool: str, args: dict, group_id: str | None = None
    ) -> dict:
        """登记一条待审批的工具请求,返回记录(重复请求刷新时间)。"""
        self._expire_tool_approvals()
        self.pending_tool_approvals[user_id] = {
            "tool": tool,
            "args": args,
            "group_id": group_id,
            "ts": time.time(),
        }
        return self.pending_tool_approvals[user_id]

    def get_pending_tool_approval(self, user_id: str) -> dict | None:
        self._expire_tool_approvals()
        return self.pending_tool_approvals.get(user_id)

    def resolve_tool_approval(self, user_id: str, approved: bool) -> dict:
        """用户答复审批:批准则一次性授权该工具,拒绝/过期则清空请求。"""
        self._expire_tool_approvals()
        record = self.pending_tool_approvals.pop(user_id, None)
        if not record:
            return {"error": "没有待审批的工具请求"}
        if approved:
            self.allow_tool(user_id, record["tool"], ttl=self._tool_approval_ttl)
        return {"ok": True, "tool": record["tool"], "approved": approved}

    def _expire_tool_approvals(self) -> None:
        now = time.time()
        stale = [
            uid
            for uid, r in self.pending_tool_approvals.items()
            if now - r.get("ts", 0) > self._tool_approval_ttl
        ]
        for uid in stale:
            self.pending_tool_approvals.pop(uid, None)

    def allow_tool(self, user_id: str, tool: str, ttl: float = 300) -> None:
        """授予一次性临时工具权限(带 TTL,定期清理防泄漏)。"""
        self._prune_temp_allows()
        self._temp_tool_allows[(user_id, tool)] = {"ts": time.time(), "ttl": ttl}

    def is_tool_allowed(self, user_id: str, tool: str) -> bool:
        self._prune_temp_allows()
        return (user_id, tool) in self._temp_tool_allows

    def _prune_temp_allows(self) -> None:
        now = time.time()
        stale = [k for k, v in self._temp_tool_allows.items() if now - v["ts"] > v["ttl"]]
        for k in stale:
            self._temp_tool_allows.pop(k, None)

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
            "approved_users": sorted(self._approved_users),
        }

    def load_dict(self, data: dict) -> None:
        self.admin_users = set(data.get("admin_users", []))
        self.super_admin_users = set(data.get("super_admin_users", []))
        self.blacklist = set(data.get("blacklist", []))
        self._approved_users = set(data.get("approved_users", []))


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


def _normalize_addr(addr) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    """IPv4-mapped IPv6(`::ffff:7f00:1` 即 127.0.0.1)转回 IPv4 再判定。"""
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        return addr.ipv4_mapped
    return addr


def _is_private_ip(addr) -> bool:
    addr = _normalize_addr(addr)
    return any(addr in ipaddress.ip_network(net) for net in _PRIVATE_NETWORKS)


def _parse_alt_ip(hostname: str) -> ipaddress.IPv4Address | None:
    """解析非标准 IP 字面量:十进制整数(2130706433)与十六进制(0x7f000001)。"""
    if re.fullmatch(r"0x[0-9a-fA-F]+", hostname):
        try:
            return ipaddress.IPv4Address(int(hostname, 16))
        except (ValueError, ipaddress.AddressValueError):
            return None
    if hostname.isdigit() and len(hostname) <= 10:
        try:
            return ipaddress.IPv4Address(int(hostname))
        except (ValueError, ipaddress.AddressValueError):
            return None
    return None


def is_private_hostname(hostname: str) -> bool:
    """判断主机名/IP 字面量是否指向内网(SSRF 防御)。

    仅对可解析的 IP 字面量(含十进制/十六进制变体、IPv4-mapped)判定;
    纯主机名返回 False,需经 DNS 解析后判定(见 `is_safe_url_async`)。
    """
    if not hostname:
        return True
    hostname = hostname.lower().rstrip(".")
    if hostname in ("localhost", "localhost.localdomain") or hostname.endswith(".local"):
        return True
    try:
        addr = ipaddress.ip_address(hostname)
    except ValueError:
        addr = _parse_alt_ip(hostname)
        if addr is None:
            return False
    return _is_private_ip(addr)


def is_safe_url(url: str) -> bool:
    """同步检查:协议白名单 + 主机名/IP 字面量内网判定。

    主机名(非 IP 字面量)在此不做 DNS 解析,仅做保守快速判断;
    需完整 DNS 防护时使用 `is_safe_url_async`(解析失败一律拒绝)。
    """
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    return not is_private_hostname(parsed.hostname or "")


async def is_safe_url_async(url: str) -> bool:
    """异步 SSRF 检查:解析 DNS 后逐地址判定,解析失败一律拒绝(fail closed)。

    覆盖:主机名解析到内网(如 localtest.me→127.0.0.1)、单一标签内网名。
    """
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    hostname = parsed.hostname or ""
    if not hostname:
        return False
    try:
        addr = ipaddress.ip_address(hostname)
    except ValueError:
        addr = _parse_alt_ip(hostname)
    if addr is not None:
        return not _is_private_ip(addr)
    if is_private_hostname(hostname):
        return False
    try:
        loop = asyncio.get_running_loop()
        infos = await loop.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
    except Exception:  # noqa: BLE001 解析失败/超时 → 拒绝
        return False
    for _family, _stype, _proto, _canonname, sockaddr in infos:
        ip = sockaddr[0]
        try:
            a = ipaddress.ip_address(ip)
        except ValueError:
            continue
        if _is_private_ip(a):
            return False
    return True
