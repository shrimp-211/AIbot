"""配置系统测试."""
import sys; from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
import os
from utils.config import Config, _resolve_env


def test_dot_notation():
    c = Config(); c._data = {"a": {"b": {"c": 42}}}
    assert c.get("a.b.c") == 42
    assert c.get("a.b.x", "default") == "default"


def test_set_method():
    c = Config(); c._data = {"a": {"b": 1}}; c.set("a.b", 99)
    assert c.get("a.b") == 99


def test_env_resolve():
    os.environ["TEST_KEY"] = "test_val"
    assert _resolve_env("prefix_${TEST_KEY}_suffix") == "prefix_test_val_suffix"
    assert _resolve_env("${MISSING:-default_val}") == "default_val"
