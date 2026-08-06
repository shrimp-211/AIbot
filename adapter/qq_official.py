"""QQ 官方开放平台 Webhook 适配器。

接收 QQ 官方机器人的事件上报(HTTP POST webhook),转换成 AgentEvent;
发送走 QQ 官方 OpenAPI(`/v2/groups|/c2c/.../messages`)。

需要配置官方凭据(app_id / app_secret),sign_secret 用于请求验签。
事件上报格式与 OneBot v11 高度相似(message 数组),因此转换逻辑复用。
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
from typing import Any

import aiohttp
from aiohttp import web
from loguru import logger

from .base import BaseAdapter
from .driver import ReverseDriver, ReverseServerMixin
from .event import AgentEvent
from .message import MessageChain


class QQOfficialAdapter(ReverseServerMixin, BaseAdapter):
    platform = "qq_official"

    _token_cache: dict[str, tuple[str, float]] = {}
    _token_ttl = 7200  # 官方 access_token 有效期(秒)

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 6197,
        path: str = "/qq-official",
        app_id: str = "",
        app_secret: str = "",
        sign_secret: str = "",
        api_base: str = "https://api.sgroup.qq.com",
        driver: ReverseDriver | None = None,
    ):
        self.token = ""  # 官方 webhook 用 sign_secret 验签,mixin 的 bearer token 不需要
        self.app_id = app_id
        self.app_secret = app_secret
        self.sign_secret = sign_secret
        self.api_base = api_base.rstrip("/")
        self._init_server(host, port, path, driver)
        self._session: aiohttp.ClientSession | None = None
        self._running = False
        self._msg_seq = 0

    def _register_driver_route(self, driver: ReverseDriver, path: str) -> None:
        driver.register_http(path, self._webhook_handler, method="POST")

    def _register_routes(self, app: web.Application) -> None:
        app.router.add_post(self.path, self._webhook_handler)

    async def start(self) -> None:
        await self._start_server("QQ 官方 Webhook")
        self._session = aiohttp.ClientSession()
        self._running = True
        if not self.sign_secret:
            logger.warning(
                "QQ 官方适配器未配置 sign_secret,webhook 验签 fail-closed(将拒绝所有事件上报)"
            )

    async def stop(self) -> None:
        self._running = False
        await self._stop_server()
        if self._session is not None:
            await self._session.close()
            self._session = None
        logger.info("QQ 官方适配器已停止")

    # ---------- 签名验证 ----------

    def _verify_signature(self, request: web.Request, body: bytes) -> bool:
        """验证 QQ 官方 webhook 签名(需配置 sign_secret)。

        未配置 sign_secret 时 fail-closed(拒绝所有请求),避免部署者误以为
        已验签;同时校验 X-Timestamp 新鲜度(±5 分钟),防重放攻击。
        """
        if not self.sign_secret:
            logger.warning("QQ 官方适配器未配置 sign_secret,拒绝 webhook 请求")
            return False
        timestamp = request.headers.get("X-Timestamp", "")
        if timestamp:
            try:
                ts = float(timestamp)
            except (TypeError, ValueError):
                logger.warning("QQ 官方 webhook 时间戳格式非法,跳过新鲜度检查")
            else:
                if abs(time.time() - ts) > 300:
                    logger.warning("QQ 官方 webhook 时间戳过期(可能重放),拒绝")
                    return False
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("sha256="):
            logger.warning("QQ 官方 webhook 缺少 sha256 签名头")
            return False
        nonce = request.headers.get("X-Nonce", "")
        calc = hashlib.sha256(
            (self.sign_secret + timestamp + nonce + body.decode("utf-8", "ignore")).encode()
        ).hexdigest()
        return hmac.compare_digest(calc, auth[len("sha256=") :])

    # ---------- 事件接收 ----------

    async def _webhook_handler(self, request: web.Request) -> web.Response:
        if not self._running:
            return web.Response(status=503, text="Adapter stopped")
        body = await request.read()
        if not self._verify_signature(request, body):
            return web.Response(status=401, text="Invalid signature")
        try:
            data = json.loads(body.decode("utf-8"))
        except json.JSONDecodeError:
            return web.Response(status=400, text="Invalid JSON")
        asyncio.create_task(self._dispatch_frame(data))
        # QQ 官方要求收到后立即返回成功响应
        return web.Response(status=200, text="success")

    async def _dispatch_frame(self, data: dict[str, Any]) -> None:
        try:
            if self.on_event is None:
                return
            post_type = data.get("post_type")
            if post_type == "message":
                await self.on_event(self._build_event(data))
            elif post_type == "notice":
                await self.on_event(self._build_notice_event(data))
            elif post_type == "request":
                await self.on_event(self._build_request_event(data))
        except Exception:  # noqa: BLE001
            logger.exception("QQ 官方事件处理异常")

    def _build_event(self, data: dict[str, Any]) -> AgentEvent:
        mtype = data.get("message_type", "group")
        user_id = str(data.get("user_id", ""))
        group_id = str(data.get("group_id", "")) if data.get("group_id") else None
        sender = data.get("sender") or {}
        message = data.get("message", [])
        self_id = str(data.get("app_id", self.app_id))

        if isinstance(message, str):
            chain = MessageChain.from_cq_string(message)
        else:
            chain = MessageChain.from_segments(message)

        is_tome = False
        if mtype == "group":
            at_qs = [str(s.data.get("qq")) for s in chain.get("at")]
            is_tome = self_id in at_qs or "all" in at_qs

        session_id = group_id if mtype == "group" else user_id
        return AgentEvent(
            platform="qq_official",
            message_type=mtype,
            group_id=group_id,
            user_id=user_id,
            sender_name=sender.get("nickname", "") or str(user_id),
            sender_role=sender.get("role", ""),
            message=chain,
            raw_message=data.get("raw_message", ""),
            message_id=data.get("message_id"),
            session_id=session_id,
            is_tome=is_tome,
            _send_callback=self._reply,
        )

    def _build_notice_event(self, data: dict[str, Any]) -> AgentEvent:
        """构造通知事件。QQ 官方事件结构与 OneBot 相似,字段尽量复用。"""
        group_id = str(data.get("group_id", "")) if data.get("group_id") else None
        user_id = str(data.get("user_id", ""))
        return AgentEvent(
            platform="qq_official",
            event_type="notice",
            notice_type=data.get("notice_type", ""),
            sub_type=data.get("sub_type", ""),
            operator_id=str(data.get("operator_id", "") or ""),
            group_id=group_id,
            user_id=user_id,
            message_id=data.get("message_id"),
            session_id=group_id or user_id,
            _send_callback=self._reply,
        )

    def _build_request_event(self, data: dict[str, Any]) -> AgentEvent:
        """构造请求事件(friend 加好友 / group 加群)。flag 用于审批回执。"""
        group_id = str(data.get("group_id", "")) if data.get("group_id") else None
        user_id = str(data.get("user_id", ""))
        request_type = data.get("request_type", "")
        return AgentEvent(
            platform="qq_official",
            event_type="request",
            notice_type=request_type,  # friend | group
            sub_type=data.get("sub_type", ""),  # group: add | invite
            flag=str(data.get("flag", "") or ""),
            group_id=group_id,
            user_id=user_id,
            raw_message=data.get("comment", ""),
            session_id=group_id or user_id,
            _send_callback=self._reply,
        )

    # ---------- 发送(QQ 官方 OpenAPI) ----------

    async def _get_access_token(self) -> str:
        """获取/缓存官方 access_token(需 app_id + app_secret)。"""
        now = time.time()
        cached = self._token_cache.get(self.app_id)
        if cached and cached[1] > now + 60:
            return cached[0]
        if self._session is None or not (self.app_id and self.app_secret):
            raise RuntimeError("QQ 官方适配器未配置 app_id/app_secret")
        async with self._session.get(
            f"https://bots.qq.com/app/getAppAccessToken",
            json={
                "appId": self.app_id,
                "clientSecret": self.app_secret,
            },
            timeout=15,
        ) as resp:
            body = await resp.json()
        token = body.get("access_token", "")
        if not token:
            raise RuntimeError(f"获取 QQ 官方 access_token 失败: {body}")
        ttl = float(body.get("expires_in", self._token_ttl))
        self._token_cache[self.app_id] = (token, now + ttl)
        return token

    async def _reply(self, event: AgentEvent, text: str, at: bool = False) -> None:
        if not text:
            return
        if event.message_type == "group" and event.group_id:
            await self._send_group(event.group_id, text)
        else:
            await self._send_private(event.user_id, text)

    async def send_message(self, event: AgentEvent, text: str, at: bool = False) -> Any:
        return await self._reply(event, text, at=at)

    def _next_msg_seq(self) -> int:
        """msg_seq 需单调递增(QQ 官方要求),用计数器而非时间戳避免同秒碰撞。"""
        self._msg_seq += 1
        return self._msg_seq

    async def _send_group(self, group_openid: str, text: str) -> Any:
        token = await self._get_access_token()
        url = f"{self.api_base}/v2/groups/{group_openid}/messages"
        payload = {
            "content": [{"type": "text", "data": {"text": text}}],
            "msg_type": 0,
            "msg_seq": self._next_msg_seq(),
        }
        headers = {"Authorization": f"QQBot {token}"}
        async with self._session.post(url, json=payload, headers=headers, timeout=15) as resp:
            body = await resp.json()
        if "code" in body and body.get("code") != 0:
            raise RuntimeError(f"QQ 官方发送失败: {body}")
        return body

    async def _send_private(self, user_openid: str, text: str) -> Any:
        token = await self._get_access_token()
        url = f"{self.api_base}/v2/users/{user_openid}/messages"
        payload = {
            "content": [{"type": "text", "data": {"text": text}}],
            "msg_type": 0,
            "msg_seq": self._next_msg_seq(),
        }
        headers = {"Authorization": f"QQBot {token}"}
        async with self._session.post(url, json=payload, headers=headers, timeout=15) as resp:
            body = await resp.json()
        if "code" in body and body.get("code") != 0:
            raise RuntimeError(f"QQ 官方发送失败: {body}")
        return body

    async def call_api(self, action: str, **params: Any) -> Any:
        raise NotImplementedError("QQ 官方适配器通过 OpenAPI 发送,不提供通用 call_api")
