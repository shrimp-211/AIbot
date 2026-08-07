"""PlatformService(本项目适配):平台注册与 webhook 回调。

平台接入的实际管理在 ``bot_service.BotConfigService``(映射本项目 config.yaml 平台段)。
本服务处理 AstrBot 兼容的注册端点与 webhook 回调。
"""

from __future__ import annotations

from typing import Any

from astrbot.core import logger
from astrbot.core.core_lifecycle import AstrBotCoreLifecycle

from .bot_service import BOT_TYPES


class PlatformServiceError(Exception):
    def __init__(self, message: str, status_code: int = 500) -> None:
        super().__init__(message)
        self.status_code = status_code


class PlatformService:
    def __init__(self, core_lifecycle: AstrBotCoreLifecycle) -> None:
        self.core_lifecycle = core_lifecycle
        self.config = core_lifecycle.astrbot_config
        self.platform_manager = core_lifecycle.platform_manager

    async def handle_platform_registration(self, bot_type: str, payload: dict) -> dict:
        """处理平台注册:把 payload 写入本项目对应平台段。"""
        if bot_type not in BOT_TYPES:
            raise PlatformServiceError(f"不支持的平台类型: {bot_type}", 400)
        config = payload.get("config") or payload
        if not isinstance(config, dict):
            raise PlatformServiceError("配置格式错误", 400)
        current = self.config.get(bot_type, {})
        if not isinstance(current, dict):
            current = {}
        merged = {**current, **config}
        if "enable" in payload and "enabled" not in merged:
            merged["enabled"] = bool(payload["enable"])
        self.config[bot_type] = merged
        try:
            self.config.save_config()
        except Exception as exc:  # noqa: BLE001
            logger.error("保存平台注册失败: %s", exc)
        return {"id": bot_type, "type": bot_type, "config": merged}

    async def handle_webhook_callback(self, webhook_uuid: str, request) -> dict:
        """平台 webhook 回调(本项目 webhook 由独立 WebhookServer 处理,此处确认接收)。"""
        return {"ok": True, "uuid": webhook_uuid}

    def get_platform_stats(self) -> dict:
        platforms = []
        for key, meta in BOT_TYPES.items():
            cfg = self.config.get(key, {}) or {}
            enabled = bool(cfg.get("enabled", False))
            platforms.append({
                "id": key,
                "type": key,
                "display_name": meta["display_name"],
                "enable": enabled,
                "connected": enabled,
            })
        return {"platforms": platforms}
