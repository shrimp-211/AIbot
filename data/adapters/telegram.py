"""Telegram 适配器插件(内置)。

基于 aiohttp 的 Bot API 长轮询,无需自建服务端口。
配置段见 config.yaml 的 `telegram`。
"""
from __future__ import annotations

from src.adapter import AdapterRegistry, BaseAdapter, TelegramAdapter
from src.utils.config import Config


def register(
    adapter_registry: AdapterRegistry,
    config: Config,
    driver=None,
) -> BaseAdapter | None:
    """按配置注册 Telegram 适配器;未启用返回 None。"""
    cfg = config.get("telegram", {})
    if not cfg.get("enabled", False) or not cfg.get("token"):
        return None
    adapter = TelegramAdapter(
        token=cfg.get("token", ""),
        allowed_chat_ids=list(cfg.get("allowed_chat_ids", []) or []),
        poll_timeout=float(cfg.get("poll_timeout", 30) or 30),
    )
    adapter_registry.register("telegram", adapter)
    return adapter
