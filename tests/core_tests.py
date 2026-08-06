"""核心模块自检(审查修复回归):不依赖真实 LLM 与外部网络。

覆盖审查中修复的项目:
- CQ 注入转义(escape_cq)
- 配对审批码复用 + 过期(防刷码)
- 插件 llm 匹配(单关键词)与分发迭代防御
- PluginStage 命令前缀标记
- ReverseDriver 路由重复注册保护 / WS+HTTP 混合挂载

运行:python -m src.tests.core_tests
"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.adapter.base import AdapterRegistry, BaseAdapter
from src.adapter.driver import ReverseDriver
from src.adapter.event import AgentEvent
from src.adapter.message import MessageChain, MessageSegment, escape_cq
from src.pipeline.stages.plugin import PluginStage
from src.plugins.registry import PluginRegistry
from src.security.auth import AuthManager
from src.utils.config import Config, load_config, save_config, validate_config


def make_event(text: str, user_id: str = "42", group_id: str | None = "100") -> AgentEvent:
    event = AgentEvent(
        platform="qq",
        message_type="group" if group_id else "private",
        group_id=group_id,
        user_id=user_id,
        sender_name="tester",
        message=MessageChain([MessageSegment.text(text)]),
        session_id=f"{group_id or user_id}:{user_id}",
        is_tome=True,
    )

    async def noop(_event, msg, at=False):
        pass

    event._send_callback = noop
    return event


async def test_escape_cq_injection():
    evil = "[CQ:at,qq=114514] [CQ:image,file=1.jpg] a&b"
    safe = escape_cq(evil)
    assert "[CQ:at" not in safe, safe
    assert "&#91;CQ:at,qq=114514&#93;" in safe, safe
    assert "&amp;" in safe, safe


async def test_pairing_reuse():
    auth = AuthManager()
    c1 = auth.request_pairing("10001", "200")
    c2 = auth.request_pairing("10001", "200")  # 同用户复用同一码
    assert c1 == c2, (c1, c2)
    c3 = auth.request_pairing("10002", "200")  # 不同用户生成新码
    assert c3 != c1, (c3, c1)
    assert len(auth.pending_approvals) == 2
    assert auth.has_pending("10001")


async def test_pairing_expire():
    auth = AuthManager()
    auth._approval_ttl = 0  # 立即过期
    code = auth.request_pairing("10003")
    time.sleep(0.01)
    assert not auth.has_pending("10003"), "过期审批码应被清理"
    assert not auth.is_paired("10003")
    auth.approve_pairing(code, "admin")  # 过期后审批应失败
    assert not auth.is_paired("10003")


async def test_pairing_approve():
    auth = AuthManager()
    code = auth.request_pairing("10004")
    res = auth.approve_pairing(code, "admin42")
    assert res.get("ok") and res["user_id"] == "10004", res
    assert auth.is_paired("10004")


async def test_llm_matcher_single_keyword():
    registry = PluginRegistry(AuthManager())

    @registry.llm("查询天气信息")
    async def weather(event: AgentEvent):
        await event.reply("天气查询")
        return None

    # 命中任一关键词即可触发(与实现/docstring 一致)
    ev = make_event("今天查询天气")
    handled = await registry.dispatch(ev)
    assert handled, "命中关键词应触发 llm 插件"


async def test_plugin_dispatch_block():
    registry = PluginRegistry(AuthManager())
    calls: list[str] = []

    @registry.message(priority=1, block=True)
    async def always(event: AgentEvent):
        calls.append("always")
        await event.reply("blocked")
        return None

    @registry.message(priority=2, block=False)
    async def never(event: AgentEvent):
        calls.append("never")
        return None

    handled = await registry.dispatch(make_event("随便"))
    assert handled is True, "block 插件应阻断后续"
    assert calls == ["always"], calls


async def test_plugin_stage_prefix_marking():
    config = Config({"pipeline": {"command_prefixes": ["!", "/"]}})
    stage = PluginStage(None, config)
    ev = make_event("/help 我")
    await stage.process(ev)
    assert ev.is_plain_command is True
    ev2 = make_event("你好")
    await stage.process(ev2)
    assert ev2.is_plain_command is False


async def test_driver_route_dedup():
    driver = ReverseDriver()
    seen: list[str] = []

    async def ws_handler(request):
        seen.append("ws")
        return request  # 占位

    async def http_handler(request):
        seen.append("http")
        return request

    driver.register_ws("/ws", ws_handler)
    driver.register_ws("/ws", ws_handler)  # 重复注册应被忽略
    driver.register_http("/hook", http_handler, method="POST")
    driver.register_http("/hook", http_handler, method="POST")
    driver.register_http("/hook", http_handler, method="GET")  # 同路径不同 method 允许
    assert ("GET", "/ws") in driver._registered
    assert ("POST", "/hook") in driver._registered
    assert ("GET", "/hook") in driver._registered
    assert len(driver._registered) == 3, driver._registered


async def test_message_recall_withdraw():
    """撤回插件:记录机器人消息 ID → 通过 AdapterRegistry 调用 delete_msg。"""
    from pathlib import Path

    class FakeAdapter(BaseAdapter):
        platform = "qq"

        def __init__(self):
            self._msgs: dict[str, int] = {}
            self.deleted: list[int] = []

        async def start(self):
            pass

        async def stop(self):
            pass

        async def send_message(self, event, text, at=False):
            return {"message_id": 111}

        def _remember_bot_message(self, key: str, mid: int) -> None:
            self._msgs[key] = int(mid)

        async def recent_bot_message(self, key: str):
            return self._msgs.get(key)

        async def forget_bot_message(self, key: str):
            self._msgs.pop(key, None)

        async def delete_msg(self, mid: int):
            self.deleted.append(int(mid))

    adapter_registry = AdapterRegistry()
    fake = FakeAdapter()
    adapter_registry.register("qq", fake)

    plug = PluginRegistry(AuthManager(admin_users=("42",)))
    plug.register_dependency(AdapterRegistry, adapter_registry)
    loaded = await plug.load_from_directory(ROOT / "src" / "data" / "plugins")
    assert "message_recall" in loaded, loaded

    replies: list[str] = []
    ev = make_event("/撤回", user_id="42", group_id="100")

    async def capture(_e, msg, at=False):
        replies.append(msg)

    ev._send_callback = capture
    fake._remember_bot_message(ev.session_id, 111)  # 模拟机器人刚发过消息

    handled = await plug.dispatch(ev)
    assert handled, "撤回命令应被插件处理"
    assert fake.deleted == [111], fake.deleted
    assert any("已撤回" in r for r in replies), replies

    # 无记录时:不调用 API,提示无消息
    ev2 = make_event("/撤回", user_id="42", group_id="100")
    ev2._send_callback = capture
    await plug.dispatch(ev2)
    assert fake.deleted == [111], "无记录时不应调用 delete_msg"


async def test_config_validate_ok():
    cfg = load_config(ROOT / "src" / "config.yaml")
    assert validate_config(cfg.raw()) == [], validate_config(cfg.raw())


async def test_config_validate_errors():
    bad = {"webui": {"port": "not-a-port"}, "agent": {"max_iterations": -3}}
    errors = validate_config(bad)
    assert any("webui.port" in e for e in errors), errors
    assert any("max_iterations" in e for e in errors), errors


async def test_config_set_get():
    cfg = Config({"a": {"b": 1}})
    assert cfg.get("a.b") == 1
    cfg.set("a.b", 2)
    cfg.set("x.y.z", "deep")
    assert cfg.get("a.b") == 2
    assert cfg.get("x.y.z") == "deep"
    assert cfg.get("missing", "fallback") == "fallback"


async def test_config_save_reload():
    from tempfile import TemporaryDirectory

    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "conf.yaml"
        data = {"webui": {"enabled": True, "port": 8080}}
        assert save_config(path, data) == []
        assert path.is_file()
        cfg = load_config(path)
        assert cfg.get("webui.port") == 8080
        # 修改内存并写回,再热重载
        cfg.set("webui.port", 9090)
        assert cfg.save() == []
        reload_errors = cfg.reload()
        assert reload_errors == [], reload_errors
        assert cfg.get("webui.port") == 9090
        # 非法配置拒绝写盘
        assert save_config(path, {"webui": {"port": "x"}}) != []


async def test_driver_start_stop_idempotent():
    driver = ReverseDriver(port=6191)
    await driver.start()
    assert driver.is_started
    await driver.start()  # 幂等
    await driver.stop()
    assert not driver.is_started
    await driver.stop()  # 幂等


# ---------- AstrBot 移植机制(A/B/C/D) ----------


class _FakeProvider:
    """可注入的假 Provider:前 fail_until 次调用抛异常。"""

    def __init__(self, name: str = "fake", fail_until: int = 0):
        self.name = name
        self.model = name
        self.config = {}
        self.fail_until = fail_until
        self.calls = 0

    async def chat(self, messages, system_prompt=None, tools=None, **kwargs):
        self.calls += 1
        if self.calls <= self.fail_until:
            raise RuntimeError(f"{self.name} down")
        return {"content": f"ok:{self.name}", "tool_calls": []}

    async def test(self):
        return True


async def test_provider_manager_fallback():
    """机制 A:主 provider 失败自动切备用,冷却到期后自愈回主。"""
    from src.providers.manager import ProviderManager

    main = _FakeProvider("main", fail_until=1)  # 仅第一次失败
    fb = _FakeProvider("fallback")
    mgr = ProviderManager(
        {"model": "main", "fallback_providers": [{"model": "fallback"}]},
        factory=lambda cfg: main if cfg.get("model") == "main" else fb,
        cooldown_secs=0,
    )
    r1 = await mgr.chat([{"role": "user", "content": "hi"}])
    assert r1["content"] == "ok:fallback", r1  # main 失败 → fallback
    assert mgr.active is fb
    r2 = await mgr.chat([{"role": "user", "content": "hi"}])
    assert r2["content"] == "ok:main", r2  # main 恢复 → 自愈
    assert mgr.active is main
    assert await mgr.test() is True

    # 冷却防抖:冷却期内不重复打主 provider
    main2 = _FakeProvider("main2", fail_until=1)
    fb2 = _FakeProvider("fallback2")
    mgr2 = ProviderManager(
        {"model": "main2", "fallback_providers": [{"model": "fallback2"}]},
        factory=lambda cfg: main2 if cfg.get("model") == "main2" else fb2,
        cooldown_secs=100,
    )
    await mgr2.chat([{"role": "user", "content": "hi"}])  # main2 失败 → fallback2
    calls_after_first = main2.calls
    await mgr2.chat([{"role": "user", "content": "hi"}])  # main2 冷却中,应跳过
    assert main2.calls == calls_after_first, "冷却期内不应重试 main"


async def test_fix_messages_pairing():
    """机制 B:assistant(tool_calls)+连续 tool 链整体保留;前无 assistant 的孤立 tool 被丢弃;无配套 tool 的 assistant(tool_calls) 被移除。"""
    from src.agent.compressor import fix_messages

    chain = [
        {"role": "user", "content": "hi"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{"id": "1", "function": {"name": "x", "arguments": "{}"}}],
        },
        {"role": "tool", "tool_call_id": "1", "content": "ok"},
        {"role": "tool", "tool_call_id": "2", "content": "second"},
    ]
    assert fix_messages(chain) == chain  # 合法链原样保留

    isolated = [
        {"role": "user", "content": "hi"},
        {"role": "tool", "tool_call_id": "orphan", "content": "孤立"},
    ]
    fixed = fix_messages(isolated)
    assert all(m.get("role") != "tool" for m in fixed), fixed  # 孤立 tool 被丢弃

    cut = [
        {"role": "user", "content": "hi"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{"id": "9", "function": {"name": "y", "arguments": "{}"}}],
        },
    ]
    fixed2 = fix_messages(cut)
    assert all(not m.get("tool_calls") for m in fixed2), fixed2  # 无配套 tool 被移除


async def test_truncate_turns_halving():
    """机制 B:按轮截断/减半兜底后,system 在前且紧跟 user。"""
    from src.agent.compressor import truncate_by_halving, truncate_by_turns

    msgs = [{"role": "system", "content": "sys"}]
    for i in range(6):
        msgs.append({"role": "user", "content": f"u{i}"})
        msgs.append({"role": "assistant", "content": f"a{i}"})

    out = truncate_by_turns(msgs, keep_most_recent_turns=2)
    assert out[0]["role"] == "system", out
    assert out[1]["role"] == "user", out  # system 后紧跟 user
    assert out[-1]["content"] == "a5", out  # 保留最近

    half = truncate_by_halving(msgs)
    assert half[0]["role"] == "system", half
    assert half[1]["role"] == "user", half
    assert len(half) <= len(msgs)
    assert half[-1]["content"] == "a5", half


async def test_compress_ensure_user():
    """机制 B:压缩后 system 摘要后必须紧跟 user,且 tool 配对完整。"""
    from src.agent.compressor import compress_messages

    class _SummaryProvider:
        async def chat(self, messages, system_prompt=None, tools=None, **kwargs):
            return {"content": "摘要内容", "tool_calls": []}

    msgs = [{"role": "user", "content": "x" * 10} for _ in range(50)]
    msgs.append(
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{"id": "1", "function": {"name": "bash", "arguments": "{}"}}],
        }
    )
    msgs.append({"role": "tool", "tool_call_id": "1", "content": "ok"})
    out = await compress_messages(
        _SummaryProvider(), msgs, max_tokens=50, keep_recent=2
    )
    assert out[0]["role"] == "system", out
    assert out[1]["role"] == "user", out  # system 后紧跟 user
    assert any(m.get("role") == "tool" for m in out), out  # tool 配对保留


async def test_repeated_tool_guidance():
    """机制 C:重复工具调用指导分级(>=3 提示,>=4 注意,>=5 警告)。"""
    from src.agent.engine import _repeated_tool_guidance

    assert _repeated_tool_guidance("bash", 1) == ""
    assert _repeated_tool_guidance("bash", 2) == ""
    assert "bash" in _repeated_tool_guidance("bash", 3)
    assert "注意" in _repeated_tool_guidance("bash", 4)
    assert "警告" in _repeated_tool_guidance("bash", 5)


async def test_cron_agent_fire():
    """机制 D:定时任务经主 Agent 生成内容;未注入引擎时走固定文本兜底。"""
    from src.agent.proactive import CronManager
    from src.utils.config import Config

    class _FakeAdapter:
        def __init__(self):
            self.sent: list[tuple] = []

        async def send_group_msg(self, gid, text):
            self.sent.append(("group", gid, text))

        async def send_private_msg(self, uid, text):
            self.sent.append(("private", uid, text))

    class _FakeEngine:
        def __init__(self):
            self.processed: list[str] = []

        async def process(self, event):
            self.processed.append(event.plain_text)
            return "agent-reply"

    adapter = _FakeAdapter()
    engine = _FakeEngine()
    cron = CronManager(adapter, Config({"cron": {"agent_enabled": True}}))
    cron.set_engine(engine)
    task = {
        "id": "t1", "text": "汇报今日新闻", "desc": "每天9点",
        "target_group": "100", "target_user": None, "session": "g:100",
    }
    await cron._fire(task)
    assert engine.processed == ["汇报今日新闻"], engine.processed
    assert ("group", "100", "agent-reply") in adapter.sent, adapter.sent
    assert cron.get_history()[-1]["ok"] is True

    # 未注入引擎:固定文本兜底
    cron2 = CronManager(adapter, Config({"cron": {"agent_enabled": True}}))
    await cron2._fire(task)
    assert ("group", "100", "⏰ 定时提醒: 汇报今日新闻") in adapter.sent, adapter.sent


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
