"""OneBot v11 反向 WebSocket 适配器插件(内置)。

以插件形式向 main.py 提供 `register()` 入口,加载平台无需修改主程序。
默认主通道(始终启用),配置段见 config.yaml 的 `onebot`。
"""
from __future__ import annotations

from loguru import logger

from src.adapter import AdapterRegistry, BaseAdapter, OneBotV11Adapter, ReverseDriver
from src.utils.config import Config


def register(
    adapter_registry: AdapterRegistry,
    config: Config,
    driver: ReverseDriver | None = None,
) -> BaseAdapter | None:
    """注册 OneBot v11 反向 WS 适配器并返回实例;禁用返回 None。"""
    cfg = config.get("onebot", {})
    if driver is not None:
        # 共享驱动模式:监听地址以 driver 配置为准,onebot.host/port 被忽略
        cfg_host = str(cfg.get("host", "127.0.0.1"))
        cfg_port = int(cfg.get("port", 6199))
        if cfg_host != driver.host or cfg_port != driver.port:
            logger.warning(
                f"onebot.host/port({cfg_host}:{cfg_port}) 在 driver 共享模式下被忽略,"
                f"实际监听 driver.host/port({driver.host}:{driver.port})"
            )
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
