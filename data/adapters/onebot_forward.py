"""OneBot v11 正向 WebSocket 客户端适配器插件(内置)。

作为客户端主动连接 OneBot 实现端(内网/容器部署),断线自动重连。
配置段见 config.yaml 的 `onebot_forward`。
"""
from __future__ import annotations

from src.adapter import AdapterRegistry, BaseAdapter, OneBotV11Client
from src.utils.config import Config


def register(
    adapter_registry: AdapterRegistry,
    config: Config,
    driver=None,
) -> BaseAdapter | None:
    """按配置注册正向 WS 客户端适配器;未启用或未配置返回 None。"""
    cfg = config.get("onebot_forward", {})
    if not cfg.get("enabled", False) or not cfg.get("url"):
        return None
    adapter = OneBotV11Client(
        url=cfg.get("url", ""),
        token=cfg.get("token", ""),
        self_id=cfg.get("self_id", ""),
    )
    adapter_registry.register("qq_forward", adapter)
    return adapter
