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


async def test_qq_send_cq_escape():
    """qq_send_image/voice:发送 CQ 码前转义外部内容,防注入(#80 回归)。"""
    from src.agent.tools.base import ToolContext
    from src.agent.tools.qq_message import QqSendImageTool, QqSendVoiceTool

    class RecordingAdapter:
        def __init__(self) -> None:
            self.sent: list[str] = []

        async def send_group_msg(self, gid, message):
            self.sent.append(message)

        async def send_private_msg(self, uid, message):
            self.sent.append(message)

    adapter = RecordingAdapter()
    event = AgentEvent(
        platform="qq",
        message_type="group",
        group_id="100",
        user_id="42",
        sender_name="tester",
        message=MessageChain([MessageSegment.text("x")]),
        session_id="100:42",
        is_tome=True,
    )
    ctx = ToolContext(event=event, adapter=adapter, auth=AuthManager(), config=Config({}), db=None)

    evil_img = "x.jpg&[CQ:image,file=evil.jpg]"
    await QqSendImageTool().execute(ctx, evil_img)
    await QqSendVoiceTool().execute(ctx, "a.mp3[CQ:at,qq=1]")
    assert len(adapter.sent) == 2
    img_msg, voice_msg = adapter.sent
    # 注入的 CQ 码被转义,外层包裹保留
    assert "[CQ:image,file=evil" not in img_msg, img_msg
    assert "&#91;CQ:image,file=evil" in img_msg and "&amp;" in img_msg, img_msg
    assert "[CQ:at,qq=1]" not in voice_msg, voice_msg
    assert "&#91;CQ:at,qq=1&#93;" in voice_msg, voice_msg


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
        _SummaryProvider(), msgs, max_tokens=50, keep_recent_ratio=0.3
    )
    # 摘要以 user/assistant 对注入(参照 AstrBot LLMSummaryCompressor)
    assert out[0]["role"] == "user" and "摘要" in out[0]["content"], out
    assert out[1]["role"] == "assistant", out  # 摘要确认
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

        def names(self):
            return [s["name"] for s in self.schemas()]

    class FakePersona:
        def get_tool_allowlist(self, sid):
            return ["web_search", "file_read"] if sid == "restricted" else None

    sr = SkillRegistry()
    engine = AgentEngine(
        provider=object(),
        tools=FakeTools(),
        memory=object(),
        auth=object(),
        config=Config({}),
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
    # 全局工具 allowlist(agent.tools_allowlist)收窄暴露面,与 persona/技能取交集
    sr.deactivate("restricted")
    engine.config.set("agent.tools_allowlist", ["web_search"])
    assert [s["name"] for s in engine._filtered_schemas("open")] == ["web_search"]
    engine.config.set("agent.tools_allowlist", None)


async def test_tool_approval_flow():
    """AuthManager 工具级交互审批:请求/批准授权/拒绝/过期清理。"""
    auth = AuthManager()
    auth.request_tool_approval("1001", "bash", {"command": "ls"})
    rec = auth.get_pending_tool_approval("1001")
    assert rec and rec["tool"] == "bash", rec
    # 批准 -> 一次性授权该工具
    res = auth.resolve_tool_approval("1001", True)
    assert res.get("ok") and auth.is_tool_allowed("1001", "bash"), res
    assert auth.get_pending_tool_approval("1001") is None
    # TTL 过期后临时授权失效(session_id=None 的全局放行)
    auth._temp_tool_allows[("1001", "bash", None)] = {"ts": 0, "ttl": 1}
    auth.is_tool_allowed("1001", "bash")  # 触发清理
    assert not auth.is_tool_allowed("1001", "bash")
    # 会话级授权:仅对指定 session 放行,不影响其他会话
    auth._temp_tool_allows[("1001", "bash", "sess-a")] = {"ts": time.time() + 100, "ttl": 100}
    assert auth.is_tool_allowed("1001", "bash", "sess-a")
    assert not auth.is_tool_allowed("1001", "bash", "sess-b")
    assert not auth.is_tool_allowed("1001", "bash")  # 全局(None)不放行
    # 拒绝 -> 清空请求且不授权
    auth.request_tool_approval("1002", "bash", {})
    auth.resolve_tool_approval("1002", False)
    assert not auth.is_tool_allowed("1002", "bash")
    # 无待审批 -> 报错
    assert "error" in auth.resolve_tool_approval("1003", True)


async def test_tool_approval_required():
    """ToolRegistry:ASK 决策抛 ToolApprovalRequired,_skip_permission 强制放行。"""
    from src.agent.tools.base import Tool, ToolApprovalRequired, ToolContext, ToolRegistry
    from src.security.auth import PermissionRule

    auth = AuthManager(rules=[PermissionRule("tool", "needs_approval", "ask")])
    registry = ToolRegistry(auth)
    event = make_event("test", user_id="42")

    class NeedApproval(Tool):
        name = "needs_approval"

        async def execute(self, ctx, **kwargs):
            return "ok"

    registry.register(NeedApproval())
    ctx = ToolContext(event=event, adapter=None, auth=auth, config=Config({}), db=None)
    raised = False
    try:
        await registry.execute("needs_approval", 1, ctx, query="x")
    except ToolApprovalRequired as exc:
        raised = True
        assert exc.tool_name == "needs_approval"
    assert raised, "ASK 工具应抛 ToolApprovalRequired"
    # 临时授权后强制放行
    result = await registry.execute(
        "needs_approval", 1, ctx, _skip_permission=True, query="x"
    )
    assert result == "ok"


async def test_classify_approval():
    """工具审批回复分类:允许/拒绝词识别,长文本不误判。"""
    from src.agent.engine import _classify_approval

    assert _classify_approval("允许") == "approve"
    assert _classify_approval("我批准执行") == "approve"
    assert _classify_approval("可以") == "approve"
    assert _classify_approval("继续") == "approve"
    assert _classify_approval("拒绝") == "deny"
    assert _classify_approval("不行") == "deny"
    assert _classify_approval("不需要") == "deny"
    # 否定式拒绝词不能被"同意/批准"子串误判为允许
    assert _classify_approval("不同意") == "deny"
    assert _classify_approval("不批准") == "deny"
    assert _classify_approval("可以帮我看看这个文件吗") is None  # 长句不误判
    assert _classify_approval("") is None
    assert _classify_approval("随便聊聊今天天气如何啊哈哈哈哈") is None


async def test_build_messages_approval():
    """审批续接:批准注入"重新调用工具"指令,拒绝注入"调整方案",均不追加原始回复。"""
    from src.agent.engine import AgentEngine

    class FakeMemory:
        def get_episodic(self, sid, limit):
            return []

        def get_working(self, sid):
            return []

    engine = AgentEngine(
        provider=object(),
        tools=object(),
        memory=FakeMemory(),
        auth=object(),
        config=object(),
        adapter=object(),
        db=object(),
        subagent_manager=object(),
        skills=object(),
    )
    # 批准:system 指令指示重新调用工具,原始"允许"不进入消息
    ev = make_event("允许")
    ev.state["approval_decision"] = "approve"
    ev.state["approval_tool"] = "bash"
    msgs = engine._build_messages(ev, "允许")
    sys_msgs = [m for m in msgs if m["role"] == "system"]
    assert any("已批准" in m["content"] and "bash" in m["content"] for m in sys_msgs)
    assert not any(m["role"] == "user" and m["content"] == "允许" for m in msgs)
    # 拒绝:system 指令指示调整方案
    ev2 = make_event("拒绝")
    ev2.state["approval_decision"] = "deny"
    ev2.state["approval_tool"] = "bash"
    msgs2 = engine._build_messages(ev2, "拒绝")
    sys_msgs2 = [m for m in msgs2 if m["role"] == "system"]
    assert any("已拒绝" in m["content"] for m in sys_msgs2)
    assert not any(m["role"] == "user" and m["content"] == "拒绝" for m in msgs2)
    # 正常流程:用户消息照常追加
    msgs3 = engine._build_messages(make_event("你好"), "你好")
    assert any(m["role"] == "user" and m["content"] == "你好" for m in msgs3)


async def test_estimate_context_window():
    """模型名 -> 上下文窗口推断。"""
    from src.providers.base import estimate_context_window

    assert estimate_context_window("deepseek-chat") == 163_840
    assert estimate_context_window("deepseek-r1") == 163_840
    assert estimate_context_window("claude-sonnet-4-6") == 1_000_000
    assert estimate_context_window("claude-3-5-sonnet") == 200_000
    assert estimate_context_window("gpt-4o") == 128_000
    assert estimate_context_window("gpt-4.1-mini") == 1_000_000
    assert estimate_context_window("o3-mini") == 200_000
    assert estimate_context_window("glm-4.5") == 128_000
    assert estimate_context_window("完全未知模型") == 128_000


async def test_provider_context_window():
    """BaseProvider.context_window:配置显式覆盖优先,否则按模型推断。"""
    from src.providers.anthropic import AnthropicProvider

    p = AnthropicProvider({"model": "deepseek-v3", "api_key": "k"})
    assert p.context_window == 163_840, p.context_window
    p2 = AnthropicProvider({"model": "x", "api_key": "k", "context_window": 99999})
    assert p2.context_window == 99999


async def test_anthropic_thinking():
    """Anthropic extended_thinking:传 thinking 参数,不传 temperature,提取 thinking block。"""
    from types import SimpleNamespace

    from src.providers.anthropic import AnthropicProvider

    class _FakeMessages:
        def __init__(self):
            self.last_params = None

        async def create(self, **params):
            self.last_params = params
            think = SimpleNamespace(type="thinking", thinking="内部推理过程")
            text = SimpleNamespace(type="text", text="最终回答")
            return SimpleNamespace(content=[think, text])

    provider = AnthropicProvider(
        {"model": "claude-sonnet-4-6", "api_key": "k",
         "thinking": {"enabled": True, "budget_tokens": 4096}}
    )
    provider._client = SimpleNamespace(messages=_FakeMessages())
    res = await provider.chat([{"role": "user", "content": "hi"}])
    assert res["content"] == "最终回答"
    assert res["thinking"] == "内部推理过程"
    assert provider._client.messages.last_params["thinking"] == {
        "type": "enabled",
        "budget_tokens": 4096,
    }
    assert "temperature" not in provider._client.messages.last_params
    # 未启用 thinking:传 temperature
    p2 = AnthropicProvider({"model": "claude-sonnet-4-6", "api_key": "k"})
    p2._client = SimpleNamespace(messages=_FakeMessages())
    await p2.chat([{"role": "user", "content": "hi"}])
    assert "thinking" not in p2._client.messages.last_params
    assert p2._client.messages.last_params.get("temperature") == p2.temperature


async def test_openai_reasoning_effort():
    """OpenAI 兼容 reasoning_effort:o 系列不传 temperature。"""
    from types import SimpleNamespace

    from src.providers.openai_compatible import OpenAICompatibleProvider

    class _FakeMsg:
        content = "hi"
        tool_calls = None

    class _FakeCompletions:
        def __init__(self):
            self.last_params = None

        async def create(self, **params):
            self.last_params = params
            return SimpleNamespace(choices=[SimpleNamespace(message=_FakeMsg())])

    provider = OpenAICompatibleProvider(
        {"model": "o3-mini", "api_key": "sk-test", "reasoning_effort": "high"}
    )
    provider._client = SimpleNamespace(chat=SimpleNamespace(completions=_FakeCompletions()))
    res = await provider.chat([{"role": "user", "content": "hi"}])
    assert res["content"] == "hi"
    assert provider._client.chat.completions.last_params.get("reasoning_effort") == "high"
    assert "temperature" not in provider._client.chat.completions.last_params
    # 未配置 reasoning_effort:传 temperature
    p2 = OpenAICompatibleProvider({"model": "gpt-4o", "api_key": "sk-test"})
    p2._client = SimpleNamespace(chat=SimpleNamespace(completions=_FakeCompletions()))
    await p2.chat([{"role": "user", "content": "hi"}])
    assert "reasoning_effort" not in p2._client.chat.completions.last_params
    assert p2._client.chat.completions.last_params.get("temperature") == p2.temperature


async def test_provider_retry():
    """provider 指数退避重试:限流可重试,耗尽抛错,非重试异常立即抛。"""
    from src.providers.base import BaseProvider

    class P(BaseProvider):
        name = "test"

        async def chat(self, messages, system_prompt=None, tools=None, **kwargs):
            return {}

        async def test(self):
            return True

    class RateLimit(Exception):
        status_code = 429

    # 首次失败(限流)重试后成功
    p = P({"model": "m", "api_key": "k", "retry": {"max_attempts": 3, "base_delay": 0.01}})
    calls = []

    async def flaky():
        calls.append(1)
        if len(calls) == 1:
            raise RateLimit()
        return "ok"

    assert await p._with_retry(flaky) == "ok"
    assert len(calls) == 2, "首次失败后应重试一次"

    # 重试耗尽仍失败
    p2 = P({"model": "m", "api_key": "k", "retry": {"max_attempts": 2, "base_delay": 0.01}})
    calls2 = []

    async def always_fail():
        calls2.append(1)
        raise RateLimit()

    try:
        await p2._with_retry(always_fail)
        assert False, "应抛出异常"
    except RateLimit:
        pass
    assert len(calls2) == 2, "重试次数应等于 max_attempts"

    # 不可重试异常立即抛,不重试
    p3 = P({"model": "m", "api_key": "k"})
    calls3 = []

    async def bad():
        calls3.append(1)
        raise ValueError("bad")

    try:
        await p3._with_retry(bad)
        assert False, "不可重试异常应直接抛出"
    except ValueError:
        pass
    assert len(calls3) == 1

    # _is_retryable 分类
    assert BaseProvider._is_retryable(RateLimit())
    assert not BaseProvider._is_retryable(ValueError("x"))
    class _ConnErr(Exception):
        pass

    _ConnErr.__module__ = "httpx"
    assert BaseProvider._is_retryable(_ConnErr())
    class _Timeout(Exception):
        pass

    _Timeout.__name__ = "ReadTimeout"
    assert BaseProvider._is_retryable(_Timeout())


# ---------- 审查修复回归(第二轮) ----------


async def test_cron_interval_persist():
    """Cron interval 任务触发后推进 next_at 并持久化,重启不再立即重触发。"""
    import os
    import tempfile

    from src.agent.proactive import CronManager
    from src.storage.db import JsonKV
    from src.utils.config import Config

    class _FakeAdapter:
        def __init__(self):
            self.sent: list[tuple] = []

        async def send_group_msg(self, gid, text):
            self.sent.append(("group", gid, text))

        async def send_private_msg(self, uid, text):
            self.sent.append(("private", uid, text))

    with tempfile.TemporaryDirectory() as d:
        db = JsonKV(os.path.join(d, "cron.json"))
        await db.initialize()
        cron = CronManager(_FakeAdapter(), Config({"cron": {"agent_enabled": False}}), db=db)
        res = await cron.add_task("s1", "每1分钟", "测试提醒", target_group="100")
        assert res.get("ok"), res
        tid = res["task_id"]

        now = time.time()
        task = cron._tasks[tid]
        task["next_at"] = now - 1  # 模拟到期,应立即触发

        await cron._check_task(tid, task, now)
        assert cron._adapter.sent, cron._adapter.sent
        assert task["next_at"] > now, "触发后应推进 next_at"
        assert db.get("cron_tasks", {}).get(tid, {}).get("next_at", 0) > now, (
            "推进后的 next_at 必须持久化,否则重启后立即重触发一次"
        )

        # 重启恢复:next_at 仍在未来,不会立即再触发
        cron2 = CronManager(_FakeAdapter(), Config({"cron": {"agent_enabled": False}}), db=db)
        await cron2.start()
        await cron2.stop()
        assert cron2._tasks[tid]["next_at"] > now


async def test_jsonkv_flush_dirty_restore():
    """JsonKV:写盘失败恢复脏标记,重试可成功(不丢失数据)。"""
    import os
    import tempfile

    from src.storage.db import JsonKV

    with tempfile.TemporaryDirectory() as d:
        db = JsonKV(os.path.join(d, "kv.json"))
        await db.initialize()
        db.set("a", 1)
        db.set("b", 2)

        original_write = db._write

        def fail_write(data):
            raise OSError("磁盘满(模拟)")

        db._write = fail_write
        raised = False
        try:
            await db.flush()
        except OSError:
            raised = True
        assert raised, "写盘失败应抛出"
        assert db._dirty is True, "写失败后脏标记应恢复,避免数据静默丢失"

        db._write = original_write
        await db.flush()
        assert db._dirty is False
        db2 = JsonKV(os.path.join(d, "kv.json"))
        await db2.initialize()
        assert db2.get("a") == 1 and db2.get("b") == 2


async def test_ssrf_edge_cases():
    """SSRF:十进制/十六进制 IP、IPv4-mapped、云元数据、localtest.me 均被拦截。"""
    from src.security.auth import is_safe_url, is_safe_url_async

    # IP 字面量变体 → 内网
    assert is_safe_url("http://2130706433/") is False  # 127.0.0.1 十进制整数
    assert is_safe_url("http://0x7f000001/") is False  # 127.0.0.1 十六进制
    assert is_safe_url("http://[::ffff:7f00:1]/") is False  # IPv4-mapped 127.0.0.1
    assert is_safe_url("http://127.0.0.1/") is False
    assert is_safe_url("http://[::1]/") is False
    assert is_safe_url("http://10.0.0.1/") is False
    assert is_safe_url("http://169.254.169.254/latest/meta-data/") is False  # 云元数据
    # 公网放行
    assert is_safe_url("https://example.com/") is True
    assert is_safe_url("http://8.8.8.8/") is True
    # 协议白名单
    assert is_safe_url("file:///etc/passwd") is False
    assert is_safe_url("ftp://example.com/") is False
    # localtest.me 解析到 127.0.0.1:同步不解析保守放行,异步 fail-closed 拒绝
    assert is_safe_url("http://localtest.me/") is True
    assert await is_safe_url_async("http://localtest.me/") is False


async def test_path_trusted_sibling_bypass():
    """可信目录边界:兄弟目录(/data/trusted_evil)与前缀相似目录不能绕过(/data/trusted)。"""
    import tempfile

    d = Path(tempfile.mkdtemp())
    (d / "trusted").mkdir()
    (d / "trusted_evil").mkdir()
    (d / "trustedX").mkdir()
    auth = AuthManager(trusted_folders=[str(d / "trusted")])
    assert auth.is_path_trusted(str(d / "trusted" / "a.txt"))
    assert auth.is_path_trusted(str(d / "trusted" / "sub" / "b.txt")), "子目录应可信"
    assert not auth.is_path_trusted(str(d / "trusted_evil" / "x.txt")), "兄弟目录不可信"
    assert not auth.is_path_trusted(str(d / "trustedX" / "y.txt")), "前缀相似目录不可信"
    # 空可信目录 = 全部路径可信
    assert AuthManager().is_path_trusted(str(d / "anywhere" / "f.txt"))


async def test_approval_stop_clears_pending():
    """审批续接顺序修复:非审批回复(STOP)放弃挂起的审批,不残留到 TTL。"""
    from src.agent.engine import AgentEngine
    from src.security.auth import AuthManager
    from src.utils.config import Config

    class FakeMemory:
        def clear_working(self, sid):
            pass

    auth = AuthManager()
    auth.request_tool_approval("42", "bash", {"command": "ls"})
    assert auth.get_pending_tool_approval("42") is not None

    engine = AgentEngine(
        provider=object(),
        tools=object(),
        memory=FakeMemory(),
        auth=auth,
        config=Config({}),
        adapter=object(),
        db=object(),
        subagent_manager=object(),
        skills=object(),
    )
    reply = await engine.process(make_event("停止", user_id="42"))
    assert reply == "已结束当前对话上下文。"
    assert auth.get_pending_tool_approval("42") is None, "STOP 应放弃挂起的审批"


async def test_approval_retained_on_unrelated():
    """审批续接(修复过度修正):无关消息保留挂起审批,不静默取消。"""
    from src.agent.engine import AgentEngine
    from src.utils.config import Config

    class FakeMemory:
        def clear_working(self, sid):
            pass

        def add_message(self, sid, role, text):
            pass

        def get_pending_question(self, sid):
            return None

        def get_profile(self, uid):
            return None

        def get_auto_memory(self, uid):
            return []

        def get_reflections(self, uid, limit=3):
            return []

        def get_episodic(self, sid, limit=8):
            return []

        def get_working(self, sid):
            return []

    class FakeSubagent:
        def bind_executor(self, executor):
            pass

    class FakeTools:
        def schemas(self):
            return []

        def names(self):
            return []

    auth = AuthManager()
    auth.request_tool_approval("42", "bash", {"command": "ls"})
    assert auth.get_pending_tool_approval("42") is not None

    engine = AgentEngine(
        provider=object(),
        tools=FakeTools(),
        memory=FakeMemory(),
        auth=auth,
        config=Config({}),
        adapter=object(),
        db=object(),
        subagent_manager=FakeSubagent(),
        skills=None,
        sqlite_store=None,
        file_memory=None,
    )
    # 无关消息:审批保持挂起,不被误结算
    reply = await engine.process(make_event("你好", user_id="42"))
    assert reply is not None
    rec = auth.get_pending_tool_approval("42")
    assert rec is not None and rec["tool"] == "bash", "无关消息不应取消挂起审批"
    # 之后用户明确批准:审批仍可正常结算并放行
    await engine.process(make_event("允许", user_id="42"))
    assert auth.get_pending_tool_approval("42") is None, "明确批准后审批应结算"
    assert auth.is_tool_allowed("42", "bash")


async def test_task_tracker_drain():
    """TaskTrackerMixin:stop 时取消在途后台任务,异常任务完成后自动回收。"""
    from src.adapter.driver import TaskTrackerMixin

    class Tracker(TaskTrackerMixin):
        def __init__(self) -> None:
            self._tasks: set[asyncio.Task] = set()

    t = Tracker()

    async def long_work():
        await asyncio.sleep(60)

    task = t._spawn(long_work())
    assert len(t._tasks) == 1
    await t._drain_tasks()
    assert len(t._tasks) == 0
    assert task.done() and task.cancelled()

    async def boom():
        raise ValueError("boom")

    task2 = t._spawn(boom())
    await asyncio.gather(task2, return_exceptions=True)  # 等 task2 完成
    await asyncio.sleep(0)  # 让 done 回调执行完毕再断言
    assert task2 not in t._tasks, "异常任务完成应从集合移除"
    assert task2.done()
    assert task2.exception() is not None, "异常已被 done 回调回收,不产生 never-retrieved 警告"


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
