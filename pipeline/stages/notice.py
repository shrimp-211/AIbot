"""通知事件阶段:群成员变动、防撤回、加好友/加群请求审批。

仅处理 event_type 为 notice / request 的事件,处理完即 stop,
不进入唤醒检测与 Agent 处理链。策略由 config.yaml 的 notice 段控制。
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable

from loguru import logger

from ...adapter.event import AgentEvent
from ...utils.config import Config
from ..scheduler import Stage

if TYPE_CHECKING:
    from ...adapter.base import BaseAdapter
    from ...storage.db import JsonKV

AdapterGetter = Callable[[], "BaseAdapter | None"]

_WELCOME_DEFAULT = "🎉 欢迎新成员 {name} 加入群聊!发送 /help 了解我能做什么~"
_FAREWELL_DEFAULT = "👋 {name} 离开了群聊"
_ANTI_RECALL_DEFAULT = "🗑️ 撤回拦截 {name}: {text}"


class NoticeStage(Stage):
    def __init__(
        self,
        config: Config,
        db: "JsonKV | None" = None,
        adapter_getter: AdapterGetter | None = None,
    ):
        self._config = config
        self._db = db
        self._adapter_getter = adapter_getter

    def _cfg(self, key: str, default: Any = None) -> Any:
        return self._config.get(f"notice.{key}", default)

    def _adapter(self) -> "BaseAdapter | None":
        return self._adapter_getter() if self._adapter_getter is not None else None

    async def process(self, event: AgentEvent) -> None:
        if event.event_type == "message":
            return
        try:
            if event.event_type == "notice":
                await self._handle_notice(event)
            elif event.event_type == "request":
                await self._handle_request(event)
        finally:
            event.stop()

    # ---------- 通知事件 ----------

    async def _handle_notice(self, event: AgentEvent) -> None:
        ntype = event.notice_type
        adapter = self._adapter()
        self_id = str(getattr(adapter, "self_id", "") or "")

        if ntype == "group_increase":
            if event.user_id and event.user_id == self_id:
                return  # 机器人自己被拉入,不需要欢迎自己
            if self._cfg("welcome.enabled", True):
                text = self._cfg("welcome.text", _WELCOME_DEFAULT)
                await self._safe_reply(event, text.format(name=event.sender_name or event.user_id))
        elif ntype == "group_decrease":
            if event.user_id and event.user_id == self_id:
                return  # 机器人自己被移出,无法播报
            if self._cfg("farewell.enabled", True):
                text = self._cfg("farewell.text", _FAREWELL_DEFAULT)
                await self._safe_reply(event, text.format(name=event.sender_name or event.user_id))
        elif ntype == "group_recall":
            await self._handle_recall(event)
        elif ntype in ("group_admin", "group_ban", "group_dismiss", "friend_add", "group_upload"):
            logger.info(
                "通知事件 %s: user=%s group=%s sub=%s operator=%s",
                ntype, event.user_id, event.group_id, event.sub_type, event.operator_id,
            )

    async def _handle_recall(self, event: AgentEvent) -> None:
        """群撤回拦截:从缓存恢复原文并重新发送。"""
        if not self._cfg("anti_recall.enabled", True):
            return
        adapter = self._adapter()
        if adapter is None:
            return
        cache = getattr(adapter, "message_cache", {})
        snapshot = cache.get(event.message_id)
        if not snapshot or snapshot.get("group_id") != event.group_id:
            return
        text = snapshot.get("text") or ""
        if not text:
            return
        name = snapshot.get("name") or "成员"
        fmt = self._cfg("anti_recall.format", _ANTI_RECALL_DEFAULT)
        try:
            await adapter.send_group_msg(event.group_id, fmt.format(name=name, text=text))
        except Exception:  # noqa: BLE001
            logger.exception("撤回拦截发送失败")

    async def _safe_reply(self, event: AgentEvent, text: str) -> None:
        if not text:
            return
        try:
            await event.reply(text)
        except Exception:  # noqa: BLE001
            logger.exception("通知回复失败")

    # ---------- 请求事件 ----------

    async def _handle_request(self, event: AgentEvent) -> None:
        adapter = self._adapter()
        if adapter is None or not event.flag:
            return
        if event.notice_type == "friend":
            policy = self._cfg("friend_requests", "accept")
            if policy == "ignore":
                return
            approve = policy == "accept"
            await adapter.set_friend_add_request(event.flag, approve=approve)
            logger.info("好友请求 %s: user=%s", "通过" if approve else "拒绝", event.user_id)
        elif event.notice_type == "group":
            policy = self._cfg("group_requests", "accept")
            if policy == "ignore":
                return
            approve = event.sub_type == "invite" or policy == "accept"
            await adapter.set_group_add_request(
                event.flag, sub_type=event.sub_type or "add", approve=approve
            )
            logger.info(
                "加群请求 %s: user=%s group=%s", "通过" if approve else "拒绝", event.user_id, event.group_id
            )
