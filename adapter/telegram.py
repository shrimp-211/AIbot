"""Telegram 适配器:基于 aiohttp 的 Bot API 长轮询(getUpdates)。

不依赖 python-telegram-bot,直接走 HTTP Bot API:
- 接收:getUpdates 长轮询(offset 确认,断线自动续拉)
- 发送:sendMessage(群聊/私聊)
- 支持 @ 提及检测、媒体消息降级为文本描述、HTML parse_mode 的 @ 回复
- allowed_chat_ids 白名单控制(空=不限制)

配置段(config.yaml):
  telegram:
    enabled: true
    token: ""            # BotFather 申请的 Bot Token
    allowed_chat_ids: [] # 允许的群/用户 ID(空=全部)
    poll_timeout: 30     # 长轮询超时秒数
"""
from __future__ import annotations

import asyncio
import html
import logging
from typing import Any

import aiohttp

from .base import BaseAdapter
from .event import AgentEvent
from .message import MessageChain

logger = logging.getLogger("adapter.telegram")

API_BASE = "https://api.telegram.org/bot{token}/"

# 媒体类型 → 降级文本描述
_MEDIA_LABELS = (
    ("photo", "[图片]"),
    ("voice", "[语音]"),
    ("video", "[视频]"),
    ("document", "[文件]"),
    ("audio", "[音频]"),
    ("sticker", "[表情]"),
    ("animation", "[动画]"),
)


class TelegramAdapter(BaseAdapter):
    platform = "telegram"

    def __init__(
        self,
        token: str = "",
        allowed_chat_ids: list[int] | None = None,
        poll_timeout: float = 30.0,
    ):
        self.token = token
        self.allowed = {int(x) for x in (allowed_chat_ids or [])}
        self.poll_timeout = max(1.0, min(float(poll_timeout or 30), 50.0))

        self._session: aiohttp.ClientSession | None = None
        self._task: asyncio.Task | None = None
        self._running = False
        self._offset = 0
        self._username = ""
        self._bot_id = 0

    # ---------- 生命周期 ----------

    async def start(self) -> None:
        if not self.token:
            logger.warning("Telegram token 为空,适配器跳过启动")
            return
        self._session = aiohttp.ClientSession()
        me = await self._api("getMe") or {}
        self._bot_id = int(me.get("id") or 0)
        self._username = (me.get("username") or "").lower()
        logger.info(
            "Telegram 适配器已启动: @%s (id=%s) | 长轮询 %.0fs | 白名单: %s",
            self._username or "?",
            self._bot_id,
            self.poll_timeout,
            "全部" if not self.allowed else sorted(self.allowed),
        )
        self._running = True
        self._task = asyncio.create_task(self._poll_loop())

    async def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        if self._session is not None:
            await self._session.close()
            self._session = None
        logger.info("Telegram 适配器已停止")

    # ---------- Bot API ----------

    async def _api(self, method: str, **params: Any) -> dict[str, Any] | None:
        if self._session is None:
            raise ConnectionError("Telegram 适配器未启动")
        url = f"{API_BASE.format(token=self.token)}{method}"
        timeout = aiohttp.ClientTimeout(total=min(self.poll_timeout + 10, 90))
        async with self._session.post(url, json=params, timeout=timeout) as resp:
            body = await resp.json(content_type=None)
        if not isinstance(body, dict) or not body.get("ok"):
            desc = body.get("description") if isinstance(body, dict) else body
            logger.warning("Telegram API %s 失败: %s", method, desc)
            return None
        return body.get("result")

    # ---------- 长轮询接收 ----------

    async def _poll_loop(self) -> None:
        while self._running:
            try:
                updates = await self._api(
                    "getUpdates",
                    offset=self._offset,
                    timeout=int(self.poll_timeout),
                    allowed_updates=["message", "edited_message"],
                )
                if updates is None:
                    await asyncio.sleep(1)
                    continue
                for u in updates:
                    update_id = u.get("update_id", 0)
                    if update_id >= self._offset:
                        self._offset = update_id + 1
                    await self._handle_update(u)
            except asyncio.CancelledError:
                break
            except Exception:  # noqa: BLE001
                logger.exception("Telegram 长轮询异常")
                await asyncio.sleep(2)

    async def _handle_update(self, update: dict[str, Any]) -> None:
        if self.on_event is None:
            return
        msg = update.get("message") or update.get("edited_message")
        if not msg:
            return
        event = self._build_event(msg)
        if self.allowed:
            key = int(event.group_id or event.user_id)
            if key not in self.allowed:
                logger.debug("Telegram 忽略非白名单会话: %s", key)
                return
        await self.on_event(event)

    def _build_event(self, msg: dict[str, Any]) -> AgentEvent:
        chat = msg.get("chat") or {}
        sender = msg.get("from") or {}
        mtype = "private" if chat.get("type") == "private" else "group"
        chat_id = str(chat.get("id", ""))
        user_id = str(sender.get("id", ""))
        text = self._extract_text(msg)
        is_tome = self._is_tome(msg, text)

        chain = MessageChain.from_cq_string(text) if text else MessageChain()
        return AgentEvent(
            platform="telegram",
            message_type=mtype,
            group_id=chat_id if mtype == "group" else None,
            user_id=user_id,
            sender_name=self._sender_name(sender),
            sender_role="",
            message=chain,
            raw_message=text,
            message_id=msg.get("message_id"),
            session_id=f"tg_{chat_id}" if mtype == "group" else f"tg_{user_id}",
            is_tome=is_tome,
            _send_callback=self._reply,
        )

    @staticmethod
    def _extract_text(msg: dict[str, Any]) -> str:
        text = (msg.get("text") or msg.get("caption") or "").strip()
        if text:
            return text
        for key, label in _MEDIA_LABELS:
            if key in msg:
                return label
        return ""

    def _is_tome(self, msg: dict[str, Any], text: str) -> bool:
        if not self._username or not text:
            return False
        for e in msg.get("entities") or []:
            if e.get("type") != "mention":
                continue
            off, ln = e.get("offset", 0), e.get("length", 0)
            if text[off: off + ln].lower() == f"@{self._username}":
                return True
        return False

    @staticmethod
    def _sender_name(sender: dict[str, Any]) -> str:
        name = f"{sender.get('first_name') or ''} {sender.get('last_name') or ''}".strip()
        if not name:
            name = f"@{sender.get('username')}" if sender.get("username") else str(sender.get("id", ""))
        return name

    # ---------- 发送 ----------

    async def _send(self, chat_id: str | int, text: str, params: dict[str, Any] | None = None) -> Any:
        """统一发送入口:失败时告警(投递问题不能静默)。"""
        payload = {"chat_id": int(chat_id), "text": str(text)[:4000]}
        if params:
            payload.update(params)
        result = await self._api("sendMessage", **payload)
        if result is None:
            logger.warning("Telegram 发送失败: chat_id=%s text=%.40s", chat_id, text)
        return result

    async def _reply(self, event: AgentEvent, text: str, at: bool = False) -> None:
        if not text:
            return
        params: dict[str, Any] = {}
        if at and event.message_type == "group" and event.user_id:
            params = {
                "text": (
                    f'<a href="tg://user?id={event.user_id}">{html.escape(event.sender_name or "你")}</a> '
                    f"{html.escape(text, quote=False)}"
                ),
                "parse_mode": "HTML",
            }
        await self._send(event.group_id or event.user_id, text, params)

    async def send_message(self, event: AgentEvent, text: str, at: bool = False) -> Any:
        return await self._reply(event, text, at=at)

    # ---------- 无 event 场景(供 Cron 等使用) ----------

    async def send_group_msg(self, group_id: str | int, message: str) -> Any:
        return await self._send(group_id, message)

    async def send_private_msg(self, user_id: str | int, message: str) -> Any:
        return await self._send(user_id, message)
