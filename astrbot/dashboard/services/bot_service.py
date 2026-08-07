"""BotConfigService(本项目适配):平台接入管理映射到本项目 config.yaml 平台段。

本项目平台段:onebot(反向 WS)/onebot_forward(正向 WS)/onebot_http(HTTP)/
qq_official(QQ 官方)/telegram。每个段含 ``enabled`` 开关与各自配置字段。
"""

from __future__ import annotations

import copy
from typing import Any

from astrbot.core import logger
from astrbot.core.core_lifecycle import AstrBotCoreLifecycle

# 本项目平台类型注册表:type -> {display_name, description, default_config, schema}
BOT_TYPES: dict[str, dict] = {
    "onebot": {
        "display_name": "OneBot v11(反向 WS)",
        "description": "OneBot 协议,机器人作为服务端,等待 NapCat/Lagrange/go-cqhttp 反向连接",
        "default_config": {"enabled": True, "host": "127.0.0.1", "port": 6199, "path": "/ws", "token": "", "self_id": ""},
        "schema": {
            "enabled": {"type": "bool", "description": "启用"},
            "host": {"type": "string", "description": "监听地址"},
            "port": {"type": "int", "description": "监听端口"},
            "path": {"type": "string", "description": "WebSocket 路径"},
            "token": {"type": "string", "description": "Access Token"},
            "self_id": {"type": "string", "description": "机器人 QQ 号"},
        },
    },
    "onebot_forward": {
        "display_name": "OneBot v11(正向 WS)",
        "description": "机器人主动连接 OneBot 实现端的 WS 服务端",
        "default_config": {"enabled": False, "url": "ws://127.0.0.1:6700", "token": "", "self_id": ""},
        "schema": {
            "enabled": {"type": "bool", "description": "启用"},
            "url": {"type": "string", "description": "OneBot 实现端 WS 地址"},
            "token": {"type": "string", "description": "Access Token"},
            "self_id": {"type": "string", "description": "机器人 QQ 号"},
        },
    },
    "onebot_http": {
        "display_name": "OneBot v11(HTTP)",
        "description": "OneBot 协议 HTTP 上报模式",
        "default_config": {"enabled": False, "host": "127.0.0.1", "port": 6199, "path": "/onebot_http", "http_url": "", "token": "", "self_id": ""},
        "schema": {
            "enabled": {"type": "bool", "description": "启用"},
            "host": {"type": "string", "description": "监听地址"},
            "port": {"type": "int", "description": "监听端口"},
            "path": {"type": "string", "description": "HTTP 路径"},
            "http_url": {"type": "string", "description": "上报地址"},
            "token": {"type": "string", "description": "Access Token"},
            "self_id": {"type": "string", "description": "机器人 QQ 号"},
        },
    },
    "qq_official": {
        "display_name": "QQ 官方机器人",
        "description": "QQ 官方机器人接口(Webhook 模式)",
        "default_config": {"enabled": False, "host": "127.0.0.1", "port": 6199, "path": "/qq_official", "app_id": "", "app_secret": "", "sign_secret": ""},
        "schema": {
            "enabled": {"type": "bool", "description": "启用"},
            "host": {"type": "string", "description": "监听地址"},
            "port": {"type": "int", "description": "监听端口"},
            "path": {"type": "string", "description": "Webhook 路径"},
            "app_id": {"type": "string", "description": "App ID"},
            "app_secret": {"type": "string", "description": "App Secret"},
            "sign_secret": {"type": "string", "description": "签名密钥"},
        },
    },
    "telegram": {
        "display_name": "Telegram",
        "description": "Telegram Bot(长轮询)",
        "default_config": {"enabled": False, "token": "", "allowed_chat_ids": [], "poll_timeout": 30},
        "schema": {
            "enabled": {"type": "bool", "description": "启用"},
            "token": {"type": "string", "description": "Bot Token"},
            "allowed_chat_ids": {"type": "list", "description": "允许的聊天 ID"},
            "poll_timeout": {"type": "int", "description": "轮询超时(秒)"},
        },
    },
}


class BotConfigServiceError(Exception):
    pass


class BotConfigService:
    def __init__(self, core_lifecycle: AstrBotCoreLifecycle) -> None:
        self.core_lifecycle = core_lifecycle
        self.config = core_lifecycle.astrbot_config

    # ---------------- 类型 ----------------

    def list_bot_types(self) -> dict:
        bot_types = []
        for key, meta in BOT_TYPES.items():
            bot_types.append({
                "type": key,
                "id": key,
                "display_name": meta["display_name"],
                "description": meta["description"],
                "default_config": copy.deepcopy(meta["default_config"]),
                "schema": copy.deepcopy(meta["schema"]),
                "support_streaming_message": True,
                "support_proactive_message": True,
            })
        return {"bot_types": bot_types}

    # ---------------- 列表/查询 ----------------

    def _all_bots(self) -> list[dict]:
        bots = []
        for key in BOT_TYPES:
            cfg = self.config.get(key, {})
            if not isinstance(cfg, dict):
                cfg = {}
            enabled = bool(cfg.get("enabled", False))
            bots.append({
                "id": key,
                "type": key,
                "enable": enabled,
                "enabled": enabled,
                "config": cfg,
            })
        return bots

    def list_bots(self, *, enabled: bool | None = None, type_: str | None = None) -> dict:
        bots = self._all_bots()
        if enabled is not None:
            bots = [b for b in bots if b["enable"] == enabled]
        if type_:
            bots = [b for b in bots if b["type"] == type_]
        return {"bots": bots}

    def get_bot(self, bot_id: str) -> dict | None:
        for b in self._all_bots():
            if b["id"] == bot_id:
                return b
        return None

    def get_bot_stats(self) -> dict:
        """平台运行统计。"""
        platforms = []
        for key, meta in BOT_TYPES.items():
            cfg = self.config.get(key, {}) or {}
            enabled = bool(cfg.get("enabled", False))
            platforms.append({
                "id": key,
                "type": key,
                "display_name": meta["display_name"],
                "enable": enabled,
                "connected": enabled,  # 本项目启动时统一加载;此处以配置为准
            })
        return {"platforms": platforms}

    # ---------------- 增删改 ----------------

    def create_bot(self, data: dict) -> dict:
        bot_type = str(data.get("type") or data.get("id") or "").strip()
        if bot_type not in BOT_TYPES:
            raise BotConfigServiceError(f"不支持的平台类型: {bot_type}")
        config = data.get("config") or {}
        if not isinstance(config, dict):
            config = {}
        # 兼容 AstrBot enable 字段
        if "enabled" not in config and "enable" in data:
            config["enabled"] = bool(data["enable"])
        merged = {**BOT_TYPES[bot_type]["default_config"], **config}
        self.config[bot_type] = merged
        try:
            self.config.save_config()
        except Exception as exc:  # noqa: BLE001
            logger.error("保存平台配置失败: %s", exc)
        return self.get_bot(bot_type) or {}

    def update_bot(self, bot_id: str, data: dict) -> dict:
        if bot_id not in BOT_TYPES:
            raise BotConfigServiceError(f"不支持的平台类型: {bot_id}")
        config = data.get("config") if isinstance(data, dict) else None
        if config is None:
            config = data
        if not isinstance(config, dict):
            raise BotConfigServiceError("配置格式错误")
        current = self.config.get(bot_id, {})
        if not isinstance(current, dict):
            current = {}
        merged = {**current, **config}
        if "enable" in data and "enabled" not in merged:
            merged["enabled"] = bool(data["enable"])
        self.config[bot_id] = merged
        try:
            self.config.save_config()
        except Exception as exc:  # noqa: BLE001
            logger.error("保存平台配置失败: %s", exc)
        return self.get_bot(bot_id) or {}

    def set_bot_enabled(self, bot_id: str, enabled: bool) -> dict:
        if bot_id not in BOT_TYPES:
            raise BotConfigServiceError(f"不支持的平台类型: {bot_id}")
        current = self.config.get(bot_id, {})
        if not isinstance(current, dict):
            current = {}
        current["enabled"] = bool(enabled)
        self.config[bot_id] = current
        try:
            self.config.save_config()
        except Exception as exc:  # noqa: BLE001
            logger.error("保存平台配置失败: %s", exc)
        return self.get_bot(bot_id) or {}

    def delete_bot(self, bot_id: str) -> None:
        if bot_id not in BOT_TYPES:
            raise BotConfigServiceError(f"不支持的平台类型: {bot_id}")
        current = self.config.get(bot_id, {})
        if isinstance(current, dict):
            current["enabled"] = False
            self.config[bot_id] = current
            try:
                self.config.save_config()
            except Exception as exc:  # noqa: BLE001
                logger.error("保存平台配置失败: %s", exc)
