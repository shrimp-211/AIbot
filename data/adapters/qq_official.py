"""QQ 官方开放平台 Webhook 适配器插件(内置)。

接收 QQ 官方机器人的事件上报(HTTP webhook,带签名验证),
发送走官方 OpenAPI。配置段见 config.yaml 的 `qq_official`。
"""
from __future__ import annotations

from src.adapter import AdapterRegistry, BaseAdapter, QQOfficialAdapter, ReverseDriver
from src.utils.config import Config


def register(
    adapter_registry: AdapterRegistry,
    config: Config,
    driver: ReverseDriver | None = None,
) -> BaseAdapter | None:
    """按配置注册 QQ 官方适配器;未启用返回 None。"""
    cfg = config.get("qq_official", {})
    if not cfg.get("enabled", False):
        return None
    adapter = QQOfficialAdapter(
        host=cfg.get("host", "127.0.0.1"),
        port=int(cfg.get("port", 6197)),
        path=cfg.get("path", "/qq-official"),
        app_id=cfg.get("app_id", ""),
        app_secret=cfg.get("app_secret", ""),
        sign_secret=cfg.get("sign_secret", ""),
        driver=driver,
    )
    adapter_registry.register("qq_official", adapter)
    return adapter
