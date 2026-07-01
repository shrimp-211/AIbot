"""权限系统测试."""
import sys; from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
import pytest
from security.auth import AuthManager, PermissionBehavior


def test_role_levels():
    auth = AuthManager(data_dir="data/test_auth")
    auth.add_super_admin("10001"); auth.add_admin("10002")
    auth._data["trusted"] = ["10003"]; auth._data["blacklist"] = ["99999"]
    assert auth.get_level("10001") == 7
    assert auth.get_level("10002") == 4
    assert auth.get_level("10003") == 1
    assert auth.get_level("12345") == 0
    assert auth.is_blacklisted("99999")


def test_permission_rules():
    auth = AuthManager(data_dir="data/test_auth"); auth._rules = []
    auth.add_rule("bash", "deny", "rm *")
    result, _ = auth.check_tool_permission("12345", "bash", {"command": "rm -rf /tmp"})
    assert result == PermissionBehavior.DENY
    auth.add_rule("bash", "allow", "ls *")
    result, _ = auth.check_tool_permission("12345", "bash", {"command": "ls -la"})
    assert result == PermissionBehavior.ALLOW


def test_pattern_matching():
    from security.auth import PermissionRule
    r = PermissionRule("file_read", "*.env", PermissionBehavior.DENY)
    assert r.matches("file_read", {"path": ".env"})
    assert r.matches("file_read", {"path": "prod.env"})
    assert not r.matches("file_read", {"path": "README.md"})
