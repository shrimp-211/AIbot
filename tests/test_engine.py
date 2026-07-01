"""Agent引擎测试."""
import sys; from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from agent.engine import AgentEngine
from agent.tools.registry import ToolRegistry
from agent.memory.store import MemoryStore
from security.auth import AuthManager


def test_plan_mode():
    from providers.openai import OpenAIProvider
    p = OpenAIProvider(model="test", api_key="test", base_url="http://localhost")
    engine = AgentEngine(provider=p, tools=ToolRegistry(), skills=None,
                          memory=MemoryStore(), auth=AuthManager(), compressor_enabled=False)
    assert not engine.is_plan_mode
    engine.enter_plan_mode(); assert engine.is_plan_mode
    engine.exit_plan_mode(); assert not engine.is_plan_mode


def test_nlu():
    engine = AgentEngine(provider=None, tools=ToolRegistry(), skills=None,
                          memory=MemoryStore(), auth=AuthManager(), compressor_enabled=False)
    assert engine._nlu_quick_check("STOP") is not None
    assert engine._nlu_quick_check("HELP") is not None
    assert engine._nlu_quick_check("你好") is None


def test_shared_react_loop():
    engine = AgentEngine(provider=None, tools=ToolRegistry(), skills=None,
                          memory=MemoryStore(), auth=AuthManager(), compressor_enabled=False)
    assert hasattr(engine, '_run_react_loop')
    assert hasattr(engine, 'enter_plan_mode')
    assert hasattr(engine, 'exit_plan_mode')
