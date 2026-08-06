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
from src.agent.hooks import HookManager
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


# ---------- 适配器修复回归 ----------


class _FakeWebRequest:
    """极简 aiohttp web.Request 替身:仅暴露适配器 handler 用到的接口。"""

    def __init__(self, body: bytes = b"{}", headers: dict | None = None):
        self._body = body
        self.headers = headers or {}

    async def json(self):
        import json as _json

        return _json.loads(self._body.decode("utf-8"))

    async def read(self):
        return self._body


async def _wait_events(collector, count: int, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if len(collector) >= count:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"等待 {count} 个事件超时,现有 {len(collector)}")


async def test_http_dispatch_notice_request():
    """HTTP 适配器:notice/request 事件应分发(群撤回拦截依赖 notice),停止后 webhook 拒绝。"""
    from src.adapter.onebot_http import OneBotV11Http

    adapter = OneBotV11Http(driver=None)
    assert adapter._running is False
    # 未启动时 webhook 直接拒绝
    resp = await adapter._webhook_handler(_FakeWebRequest(b'{"post_type":"message"}'))
    assert resp.status == 503, resp.status

    received: list[AgentEvent] = []

    async def on_event(ev):
        received.append(ev)

    adapter.on_event = on_event
    adapter._running = True  # 手工标记运行,不启动真实端口

    await adapter._dispatch_frame(
        {"post_type": "message", "message_type": "group", "group_id": 100,
         "user_id": 1, "message": [{"type": "text", "data": {"text": "hi"}}],
         "raw_message": "hi"}
    )
    await adapter._dispatch_frame(
        {"post_type": "notice", "notice_type": "group_recall", "group_id": 100,
         "user_id": 2, "operator_id": 3, "message_id": 888}
    )
    await adapter._dispatch_frame(
        {"post_type": "request", "request_type": "group", "group_id": 100,
         "user_id": 4, "flag": "abc", "comment": "加群"}
    )
    assert len(received) == 3, len(received)
    assert received[0].message_type == "group"
    assert received[1].event_type == "notice" and received[1].notice_type == "group_recall"
    assert received[1].operator_id == "3" and received[1].message_id == 888
    assert received[2].event_type == "request" and received[2].flag == "abc"

    # 鉴权:配置 token 后错误 Authorization 拒绝
    adapter.token = "sekrit"
    resp = await adapter._webhook_handler(_FakeWebRequest(b"{}", headers={"Authorization": "Bearer wrong"}))
    assert resp.status == 401, resp.status


async def test_forward_dispatch_notice_request():
    """正向 WS 客户端:notice/request 事件应分发,不再静默丢弃。"""
    from src.adapter.onebot_forward import OneBotV11Client

    client = OneBotV11Client(url="ws://127.0.0.1:1")
    received: list[AgentEvent] = []

    async def on_event(ev):
        received.append(ev)

    client.on_event = on_event

    client._on_frame(
        {"post_type": "notice", "notice_type": "group_recall", "group_id": 100,
         "user_id": 2, "operator_id": 3, "message_id": 888}
    )
    client._on_frame(
        {"post_type": "request", "request_type": "friend", "user_id": 4, "flag": "f1"}
    )
    await _wait_events(received, 2)
    assert received[0].event_type == "notice" and received[0].notice_type == "group_recall"
    assert received[1].event_type == "request" and received[1].flag == "f1"

    # echo 响应不进入 on_event
    received.clear()
    fut: asyncio.Future = asyncio.get_running_loop().create_future()
    client._echo_waiters["1"] = fut
    client._on_frame({"echo": "1", "status": "ok", "data": {"message_id": 999}})
    assert fut.done() and fut.result()["data"]["message_id"] == 999
    assert not received


async def test_forward_stop_fails_waiters():
    """正向 WS 客户端:停止时在途 API 调用立即失败(不悬挂至 30s 超时)。"""
    from src.adapter.onebot_forward import OneBotV11Client

    client = OneBotV11Client(url="ws://127.0.0.1:1")
    fut: asyncio.Future = asyncio.get_running_loop().create_future()
    client._echo_waiters["7"] = fut
    await client.stop()
    assert fut.done()
    assert isinstance(fut.exception(), ConnectionError)


async def test_qq_official_stop_and_msg_seq():
    """QQ 官方适配器:停止后 webhook 拒绝;msg_seq 单调递增。"""
    from src.adapter.qq_official import QQOfficialAdapter

    adapter = QQOfficialAdapter(driver=None)
    assert adapter._running is False
    resp = await adapter._webhook_handler(_FakeWebRequest(b"{}"))
    assert resp.status == 503, resp.status

    # msg_seq 单调递增(QQ 官方要求,同秒多条不碰撞)
    seqs = [adapter._next_msg_seq() for _ in range(5)]
    assert seqs == [1, 2, 3, 4, 5], seqs


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


async def test_hooks_pre_tool_fail_closed():
    """pre_tool_use 钩子异常时按 block 处理(fail-closed),不能漏放危险操作。"""

    async def bad(**kwargs):
        raise RuntimeError("boom")

    hm = HookManager()
    hm.register("pre_tool_use", bad)
    assert await hm.trigger("pre_tool_use", tool_name="bash") == "block"


async def test_jsonkv_initialize_delete():
    """JsonKV:async initialize 加载 + delete + 惰性加载回退。"""
    import os
    import tempfile

    from src.storage.db import JsonKV

    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "kv.json")
        db = JsonKV(p)
        await db.initialize()
        db.set("a", 1)
        await db.flush()
        db2 = JsonKV(p)
        await db2.initialize()
        assert db2.get("a") == 1
        db2.delete("a")
        assert not db2.has("a")
        await db2.flush()
        db3 = JsonKV(p)
        assert db3.get("a") is None  # 未 initialize 的惰性加载路径


async def test_subagent_executor_snapshot():
    """子代理 executor 应在 submit 时刻快照,跨消息 rebind 不影响运行中的子代理。"""
    import asyncio

    from src.agent.subagent import SubagentManager

    used: list[str] = []

    async def executor_a(name, args, flt):
        used.append("A")
        return "A-result"

    async def executor_b(name, args, flt):
        used.append("B")
        return "B-result"

    class FakeProvider:
        def __init__(self):
            self.turn = 0

        async def chat(self, messages, system_prompt=None, tools=None):
            self.turn += 1
            if self.turn == 1:
                return {
                    "content": "",
                    "tool_calls": [{"id": "t1", "name": "web_search", "arguments": {}}],
                }
            return {"content": "final", "tool_calls": []}

    class FakeConfig:
        def get(self, key, default):
            return default

    mgr = SubagentManager(FakeProvider(), FakeConfig())
    mgr.bind_executor(executor_a)
    job = mgr.submit("调研", "general", [], 1)
    mgr.bind_executor(executor_b)  # 模拟下一消息 rebind
    await mgr._jobs[job]
    result = mgr._results[job]
    assert result["status"] == "done", result
    assert used == ["A"], f"executor 应在提交时快照,实际使用: {used}"


async def test_skills_active_prune():
    """技能会话激活 + 摊销清理过期绑定。"""
    from src.agent.skills import Skill, SkillRegistry

    sr = SkillRegistry()
    sr._skills["web"] = Skill(name="web", description="调研", tools=["web_search"])
    sr.activate("s1", "web")
    assert sr.active("s1").name == "web"
    assert sr.tool_filter("s1") == ["web_search"]
    sr.deactivate("s1")
    assert sr.active("s1") is None
    sr.activate("stale", "web")
    sr._active["stale"]["ts"] = 0
    sr._access_count = 127
    sr.active("other")
    assert "stale" not in sr._active


async def test_persona_switch_prune():
    """人格切换 + 摊销清理过期会话绑定。"""
    import os
    import tempfile

    from src.agent.persona import PersonaManager
    from src.storage.db import JsonKV

    with tempfile.TemporaryDirectory() as d:
        db = JsonKV(os.path.join(d, "p.json"))
        await db.initialize()
        pm = PersonaManager(db)
        pid = pm.create(name="P", system_prompt="X")["id"]
        pm.switch("s1", pid)
        assert pm.get_prompt("s1") == "X"
        pm.switch("s1", None)
        assert pm.get_prompt("s1") != "X"
        pm.switch("stale", pid)
        pm._session_personas["stale"]["ts"] = 0
        pm._access_count = 127
        pm.get_prompt("nope")
        assert "stale" not in pm._session_personas


async def test_engine_filtered_schemas():
    """engine._filtered_schemas: persona 白名单 / 技能白名单交集 / plan_only 只读门禁。"""
    from src.agent.engine import AgentEngine
    from src.agent.skills import Skill, SkillRegistry

    class FakeTools:
        def schemas(self):
            return [
                {"name": "web_search", "description": ""},
                {"name": "file_read", "description": ""},
                {"name": "bash", "description": ""},
            ]

    class FakePersona:
        def get_tool_allowlist(self, sid):
            return ["web_search", "file_read"] if sid == "restricted" else None

    sr = SkillRegistry()
    engine = AgentEngine(
        provider=object(),
        tools=FakeTools(),
        memory=object(),
        auth=object(),
        config=object(),
        adapter=object(),
        db=object(),
        persona_manager=FakePersona(),
        subagent_manager=object(),
        skills=sr,
    )
    # 无激活技能:persona 白名单生效
    assert [s["name"] for s in engine._filtered_schemas("restricted")] == [
        "web_search",
        "file_read",
    ]
    # 无 persona 限制:全部工具
    assert [s["name"] for s in engine._filtered_schemas("open")] == [
        "web_search",
        "file_read",
        "bash",
    ]
    # plan_only 技能:收敛到只读集
    sr._skills["plan"] = Skill(name="plan", description="", tools=None, plan_only=True)
    sr.activate("s2", "plan")
    assert [s["name"] for s in engine._filtered_schemas("s2")] == ["web_search", "file_read"]
    # 技能白名单 ∩ persona 白名单
    sr.deactivate("s2")
    sr._skills["web"] = Skill(name="web", description="", tools=["web_search", "bash"])
    sr.activate("restricted", "web")
    assert [s["name"] for s in engine._filtered_schemas("restricted")] == ["web_search"]


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
