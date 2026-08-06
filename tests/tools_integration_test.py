"""工具集成自检(Phase 8):不依赖真实 LLM 与外部网络。

覆盖:
- 工具注册表完整性 / schema 可序列化
- 核心工具执行(file/grep/bash/task)
- 权限拦截(角色等级门禁)与危险命令/SSRF 防护
- Mock LLM 的 ReAct 循环(engine.process)

运行:python -m src.tests.tools_integration_test
"""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.adapter.event import AgentEvent
from src.adapter.message import MessageChain, MessageSegment
from src.agent.engine import AgentEngine
from src.agent.memory.store import MemoryStore
from src.agent.tools import build_default_registry
from src.agent.tools.base import Tool, ToolContext
from src.providers.base import BaseProvider
from src.security.auth import AuthManager, Decision, is_safe_url
from src.storage.db import JsonKV
from src.utils.config import Config


class MockProvider(BaseProvider):
    name = "mock"

    def __init__(self) -> None:
        super().__init__({"model": "mock-model", "max_tokens": 512})

    async def chat(self, messages, system_prompt=None, tools=None, **kwargs):
        return {"content": "这是 mock 的最终回复。", "tool_calls": []}

    async def test(self) -> bool:
        return True


class _AdminOnly(Tool):
    name = "admin_only"
    permission_level = 7

    async def execute(self, ctx: ToolContext, **kwargs):
        return "ok"


# ---------- 环境构造 ----------

async def make_env():
    auth = AuthManager(super_admin_users=("42",))
    tmp = tempfile.TemporaryDirectory()
    db = JsonKV(Path(tmp.name) / "kv.json")
    db.start()
    event = AgentEvent(
        platform="qq",
        message_type="group",
        group_id="100",
        user_id="42",
        sender_name="tester",
        message=MessageChain([MessageSegment.text("test")]),
        session_id="test:42",
        is_tome=True,
    )

    async def noop(_event, text, at=False):
        pass

    event._send_callback = noop
    ctx = ToolContext(
        event=event,
        adapter=None,
        auth=auth,
        config=Config({}),
        db=db,
        memory=MemoryStore(db),
        extra={"loop": asyncio.get_running_loop()},
    )
    return auth, db, tmp, ctx


# ---------- 测试用例 ----------

async def test_registry_build():
    reg = build_default_registry(AuthManager())
    names = reg.names()
    assert len(names) >= 30, f"工具数不足: {len(names)}"
    for s in reg.schemas():
        assert s["name"], s
        assert s["description"], s
        assert "parameters" in s


async def test_tool_schemas_jsonable():
    reg = build_default_registry(AuthManager())
    for s in reg.schemas():
        json.dumps(s, ensure_ascii=False)  # 无异常即通过


async def test_ssrf_block_private():
    bad = (
        "http://127.0.0.1:8080/x",
        "http://192.168.1.1/x",
        "http://10.0.0.5/",
        "http://172.16.3.4/",
        "http://169.254.0.1/",
        "http://localhost/x",
        "file:///etc/passwd",
        "ftp://example.com/x",
    )
    for url in bad:
        assert not is_safe_url(url), f"应拦截: {url}"
    assert is_safe_url("https://example.com/path")
    assert is_safe_url("http://8.8.8.8/")


async def test_command_rules():
    auth = AuthManager()
    assert auth.check_command("rm -rf /*", 7) == Decision.DENY
    assert auth.check_command("shutdown now", 7) == Decision.DENY
    assert auth.check_command("curl https://x.com", 7) == Decision.ASK
    assert auth.check_command("sudo apt update", 7) == Decision.ASK
    assert auth.check_command("echo hi", 7) == Decision.ALLOW


async def test_bash_safe():
    auth, db, tmp, ctx = await make_env()
    reg = build_default_registry(auth)
    r = await reg.execute("bash", 7, ctx, command="echo hello-42")
    assert r.get("exit_code") == 0, r
    assert "hello-42" in r.get("stdout", ""), r


async def test_bash_danger_blocked():
    auth, db, tmp, ctx = await make_env()
    reg = build_default_registry(auth)
    r = await reg.execute("bash", 7, ctx, command="rm -rf /*")
    assert isinstance(r, dict) and r.get("error"), r


async def test_bash_ask_denied_for_normal_user():
    auth, db, tmp, ctx = await make_env()
    # 普通用户(非管理员)执行 curl 应被拒绝
    ctx.event.user_id = "999"
    reg = build_default_registry(auth)
    r = await reg.execute("bash", auth.get_role_level("999", "100"), ctx, command="curl https://x.com")
    assert isinstance(r, dict) and r.get("error"), r


async def test_file_roundtrip():
    auth, db, tmp, ctx = await make_env()
    reg = build_default_registry(auth)
    p = str(Path(tmp.name) / "notes" / "a.txt")
    r = await reg.execute("file_write", 7, ctx, path=p, content="你好, world")
    assert r.get("ok"), r
    r = await reg.execute("file_read", 7, ctx, path=p)
    assert "你好" in r.get("content", ""), r
    await reg.execute("glob", 7, ctx, pattern="**/*.txt", path=tmp.name)  # 不应抛异常


async def test_grep():
    auth, db, tmp, ctx = await make_env()
    reg = build_default_registry(auth)
    p = str(Path(tmp.name) / "b.txt")
    await reg.execute("file_write", 7, ctx, path=p, content="needle42\nother\n")
    r = await reg.execute("grep", 7, ctx, pattern="needle", path=p)
    assert isinstance(r, (dict, list)), r


async def test_task_tools():
    auth, db, tmp, ctx = await make_env()
    reg = build_default_registry(auth)
    r = await reg.execute("task", 7, ctx, sub_action="create", title="自检任务")
    assert isinstance(r, dict) and r.get("ok"), r
    tid = r["task"]["task_id"]
    r2 = await reg.execute("task", 7, ctx, sub_action="list")
    assert isinstance(r2, dict), r2
    r3 = await reg.execute("task", 7, ctx, sub_action="update", task_id=tid, status="completed")
    assert isinstance(r3, dict) and r3["task"]["status"] == "completed", r3
    r4 = await reg.execute("task", 7, ctx, sub_action="get", task_id=tid)
    assert r4["task"]["status"] == "completed", r4


async def test_permission_gate():
    auth, db, tmp, ctx = await make_env()
    reg = build_default_registry(auth)
    reg.register(_AdminOnly())
    try:
        await reg.execute("admin_only", 1, ctx)
        assert False, "低角色不应通过管理员工具"
    except PermissionError:
        pass
    r = await reg.execute("admin_only", 7, ctx)
    assert r == "ok"


async def test_react_loop_mock():
    auth, db, tmp, ctx = await make_env()
    reg = build_default_registry(auth)
    engine = AgentEngine(
        provider=MockProvider(),
        tools=reg,
        memory=MemoryStore(db),
        auth=auth,
        config=Config({}),
        adapter=None,
        db=db,
    )
    reply = await engine.process(ctx.event)
    assert reply == "这是 mock 的最终回复。", reply


async def test_parallel_tool_pairing():
    """并行工具调用:所有 tool 结果在下一轮 LLM 调用前完整配对(#77 回归)。

    修复前:任一工具挂起(ask_user/审批)时提前 return,会丢弃其余并行工具的
    tool 结果,下一轮历史出现"assistant 声明 N 个 tool_calls 却只有 <N 个
    tool 结果"的配对缺失,各 provider 均拒绝该请求。
    """
    auth, db, tmp, ctx = await make_env()
    reg = build_default_registry(auth)

    class PairingProvider(BaseProvider):
        def __init__(self) -> None:
            super().__init__({"model": "mock", "max_tokens": 512})
            self.calls = 0

        async def test(self) -> bool:
            return True

        async def chat(self, messages, system_prompt=None, tools=None, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return {
                    "content": "",
                    "tool_calls": [
                        {"id": "call_a", "name": "task", "arguments": {"sub_action": "create", "title": "并行A"}},
                        {"id": "call_b", "name": "task", "arguments": {"sub_action": "create", "title": "并行B"}},
                    ],
                }
            # 第二轮:必须能看到两个 tool 结果,否则配对不完整
            tool_ids = {m.get("tool_call_id") for m in messages if m.get("role") == "tool"}
            assert {"call_a", "call_b"} <= tool_ids, f"并行工具结果未完整配对: {tool_ids}"
            return {"content": "并行任务已全部完成。", "tool_calls": []}

    engine = AgentEngine(
        provider=PairingProvider(),
        tools=reg,
        memory=MemoryStore(db),
        auth=auth,
        config=Config({}),
        adapter=None,
        db=db,
    )
    reply = await engine.process(ctx.event)
    assert reply == "并行任务已全部完成。", reply


# ---------- 运行器 ----------

async def run_all() -> bool:
    tests = [
        v
        for k, v in sorted(globals().items())
        if k.startswith("test_") and callable(v)
    ]
    passed = failed = 0
    for fn in tests:
        try:
            await fn()
            print(f"  PASS  {fn.__name__}")
            passed += 1
        except Exception as exc:  # noqa: BLE001
            print(f"  FAIL  {fn.__name__}: {type(exc).__name__}: {exc}")
            failed += 1
    print(f"\n结果: {passed} 通过, {failed} 失败, 共 {len(tests)} 项")
    return failed == 0


def main() -> None:
    ok = asyncio.run(run_all())
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
