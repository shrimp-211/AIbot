"""OneBot v11 HTTP 上报适配器插件(内置)。

webhook 接收事件 + HTTP API 调用,适用于走 HTTP 上报的部署。
配置段见 config.yaml 的 `onebot_http`。
"""
from __future__ import annotations

from src.adapter import AdapterRegistry, BaseAdapter, OneBotV11Http, ReverseDriver
from src.utils.config import Config


def register(
    adapter_registry: AdapterRegistry,
    config: Config,
    driver: ReverseDriver | None = None,
) -> BaseAdapter | None:
    """按配置注册 HTTP 上报适配器;未启用返回 None。"""
    cfg = config.get("onebot_http", {})
    if not cfg.get("enabled", False):
        return None
    adapter = OneBotV11Http(
        host=cfg.get("host", "127.0.0.1"),
        port=int(cfg.get("port", 6198)),
        path=cfg.get("path", "/onebot"),
        http_url=cfg.get("http_url", ""),
        token=cfg.get("token", ""),
        self_id=cfg.get("self_id", ""),
        driver=driver,
    )
    adapter_registry.register("qq_http", adapter)
    return adapter
