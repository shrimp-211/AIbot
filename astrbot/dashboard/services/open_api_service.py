"""OpenApiService(本项目适配):基于 API Key 的开放聊天 API。

第三方通过 API Key 调用聊天(HTTP SSE / WebSocket)。核心能力复用 ChatService
(映射本项目引擎);本服务负责 API Key 会话解析与 WS 桥接。
"""

from __future__ import annotations

import asyncio
from typing import Any

from astrbot.core import logger
from astrbot.core.core_lifecycle import AstrBotCoreLifecycle
from astrbot.core.workspace import API_KEY_USERNAME_PREFIX

from .chat_service import ChatService


class OpenApiServiceError(Exception):
    pass


class OpenApiWebSocketChatBridge:
    """开放 API 的 WebSocket 聊天桥:把 WS 消息转为 ChatService 流程。"""

    def __init__(
        self,
        build_user_message_parts=None,
        create_attachment_from_file=None,
        extract_web_search_refs=None,
        insert_user_message=None,
        save_bot_message=None,
        **kwargs,
    ) -> None:
        self.build_user_message_parts = build_user_message_parts
        self.create_attachment_from_file = create_attachment_from_file
        self.extract_web_search_refs = extract_web_search_refs
        self.insert_user_message = insert_user_message
        self.save_bot_message = save_bot_message


class OpenApiService:
    def __init__(self, db, core_lifecycle: AstrBotCoreLifecycle) -> None:
        self.db = db
        self.core_lifecycle = core_lifecycle
        self.config = core_lifecycle.astrbot_config
        self.engine = getattr(core_lifecycle, "_engine", None)

    # ---------------- 配置 ----------------

    def get_chat_config_list(self) -> list[dict]:
        provider = self.config.get("llm.provider", {}) if self.config else {}
        if not isinstance(provider, dict):
            provider = {}
        return [{
            "config_id": "default",
            "name": "默认模型",
            "model": provider.get("model", ""),
            "provider_type": "chat_completion",
        }]

    def get_chat_configs(self) -> dict:
        return {"configs": self.get_chat_config_list()}

    def get_bots(self) -> dict:
        bots = []
        for key in ("onebot", "qq_official", "telegram"):
            cfg = self.config.get(key, {}) if self.config else {}
            if isinstance(cfg, dict) and cfg.get("enabled"):
                bots.append({"id": key, "name": key, "platform": key})
        return {"bots": bots}

    # ---------------- 会话解析 ----------------

    async def prepare_chat_send(
        self,
        post_data: dict,
        config_list: list[dict],
        allow_admin_username: bool = False,
    ) -> tuple[str, str, str | None]:
        """解析有效用户名/会话/配置。返回 (username, session_id, config_id)。"""
        username = str(post_data.get("user_id") or post_data.get("username") or "api_user")
        session_id = str(post_data.get("session_id") or "default")
        config_id = post_data.get("config_id")
        if config_id is None and config_list:
            config_id = config_list[0].get("config_id")
        return username, session_id, config_id

    async def update_session_config_route(
        self, username: str, session_id: str, config_id: str | None
    ) -> str | None:
        """绑定会话到配置(本项目单配置,无需操作)。"""
        return None

    async def insert_webchat_user_message(
        self, session_id: str, effective_username: str, message_parts: list
    ) -> None:
        return None

    async def get_chat_sessions(
        self, *, username: str | None, page=1, page_size=20, platform_id: str | None = None
    ) -> list[dict]:
        return []

    async def get_chat_sessions_from_dashboard_query(
        self, *, username: str | None, page, page_size, platform_id: str | None
    ) -> list[dict]:
        return await self.get_chat_sessions(username=username, page=page, page_size=page_size, platform_id=platform_id)

    # ---------------- IM 消息 ----------------

    async def send_message(self, post_data: object) -> None:
        """开放 API 的 IM 发送(本项目:通过适配器广播到指定平台)。"""
        data = getattr(post_data, "model_dump", lambda: post_data)() or {}
        platform = str(data.get("platform") or data.get("bot_id") or "onebot")
        user_id = str(data.get("user_id") or "")
        message = str(data.get("message") or data.get("text") or "")
        if not message:
            raise OpenApiServiceError("消息为空")
        adapter_registry = getattr(self.core_lifecycle, "platform_manager", None)
        if adapter_registry is not None and hasattr(adapter_registry, "_adapter_registry"):
            registry = adapter_registry._adapter_registry
            adapter = registry.get(platform) if registry else None
            if adapter is not None:
                try:
                    await adapter.send_text(user_id, message)
                    return
                except Exception as exc:  # noqa: BLE001
                    logger.error("开放 API 发送消息失败: %s", exc)
                    raise OpenApiServiceError(f"发送失败: {exc}") from exc
        # 无适配器时记录日志
        logger.info("开放 API IM 消息(未投递): platform=%s user=%s text=%s", platform, user_id, message[:50])
        raise OpenApiServiceError("目标平台适配器未启用")

    # ---------------- WebSocket ----------------

    async def run_chat_websocket(
        self,
        raw_api_key: str | None,
        receive_json,
        send_json,
        close,
        conf_list: list[dict],
        chat_bridge: OpenApiWebSocketChatBridge,
    ) -> None:
        """开放 API 的 WebSocket 聊天(对接本项目引擎)。"""
        username = f"{API_KEY_USERNAME_PREFIX}openapi" if raw_api_key else "openapi"
        try:
            await send_json({"type": "session_ready", "data": {"session_id": "default"}})
        except Exception:
            return
        try:
            while True:
                try:
                    msg = await receive_json()
                except Exception:
                    break
                if not isinstance(msg, dict):
                    continue
                text = str(msg.get("message") or "")
                if not text.strip():
                    continue
                if self.engine is None:
                    await send_json({"type": "error", "data": "引擎未配置"})
                    continue
                # 直接调用引擎(参考 ChatService)
                import re

                safe_user = "webui_" + re.sub(r"[^A-Za-z0-9_\-]", "_", username)[:32]
                from src.adapter.event import AgentEvent
                from src.adapter.message import MessageChain, MessageSegment

                queue: asyncio.Queue = asyncio.Queue()

                event = AgentEvent(
                    message_type="private",
                    user_id=safe_user,
                    sender_name=safe_user,
                    session_id=f"webui:{safe_user}:openapi",
                    message=MessageChain([MessageSegment.text(text)]),
                    is_tome=True,
                )

                def _stream(content: str, reasoning: str) -> None:
                    if content:
                        queue.put_nowait(("plain", content))
                    elif reasoning:
                        queue.put_nowait(("reasoning", reasoning))

                event._stream_callback = _stream
                event._tool_callback = lambda *a: None
                event._send_callback = lambda *a, **k: None

                task = asyncio.create_task(self.engine.process(event))
                reply_parts = []
                while True:
                    kind, data = await queue.get()
                    if kind == "plain":
                        reply_parts.append(data)
                        await send_json({"type": "delta", "data": data})
                    elif kind == "reasoning":
                        await send_json({"type": "reasoning", "data": data})
                    if task.done() and queue.empty():
                        break
                reply = "".join(reply_parts)
                await send_json({"type": "message", "data": {"content": reply}})
        except asyncio.CancelledError:
            pass
        except Exception as exc:  # noqa: BLE001
            logger.error("开放 API WS 错误: %s", exc)
            try:
                await close(1011, "internal error")
            except Exception:
                pass
