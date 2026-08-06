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
from src.utils.config import Config


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


async def test_driver_start_stop_idempotent():
    driver = ReverseDriver(port=6191)
    await driver.start()
    assert driver.is_started
    await driver.start()  # 幂等
    await driver.stop()
    assert not driver.is_started
    await driver.stop()  # 幂等


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
