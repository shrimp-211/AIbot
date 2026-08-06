"""OneBot v11 反向 WebSocket 适配器插件(内置)。

以插件形式向 main.py 提供 `register()` 入口,加载平台无需修改主程序。
默认主通道(始终启用),配置段见 config.yaml 的 `onebot`。
"""
from __future__ import annotations

from src.adapter import AdapterRegistry, BaseAdapter, OneBotV11Adapter, ReverseDriver
from src.utils.config import Config


def register(
    adapter_registry: AdapterRegistry,
    config: Config,
    driver: ReverseDriver | None = None,
) -> BaseAdapter | None:
    """注册 OneBot v11 反向 WS 适配器并返回实例;禁用返回 None。"""
    cfg = config.get("onebot", {})
    adapter = OneBotV11Adapter(
        host=cfg.get("host", "127.0.0.1"),
        port=int(cfg.get("port", 6199)),
        path=cfg.get("path", "/ws"),
        token=cfg.get("token", ""),
        self_id=cfg.get("self_id", ""),
        driver=driver,
    )
    adapter_registry.register("qq", adapter)
    return adapter
