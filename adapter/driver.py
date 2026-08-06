"""反向驱动(Reverse Driver):托管所有被动接收连接的服务端。

参考 NoneBot2 Driver 抽象:单个 aiohttp 应用承载多个适配器的
WebSocket / HTTP 路由,避免每个适配器各自开端口、互不感知。

- register_ws(path, handler):注册 WebSocket 服务端路由(GET)
- register_http(path, handler, method):注册 HTTP webhook 路由
- start()/stop():统一启动/关闭(idempotent)

主动发起连接的适配器(正向 WS 客户端、Telegram 长轮询)不依赖本驱动。

`ReverseServerMixin` 供被动接收类适配器复用 driver/自持端口两套生命周期。
"""
from __future__ import annotations

import hmac
from typing import Any, Callable

from aiohttp import web
from loguru import logger

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


class ReverseDriver:
    """被动接收类适配器共享的 HTTP/WS 服务端。"""

    def __init__(self, host: str = "127.0.0.1", port: int = 6199):
        self.host = host
        self.port = port
        self._app = web.Application()
        self._runner = web.AppRunner(self._app)
        self._site: web.TCPSite | None = None
        self._started = False
        self._registered: set[tuple[str, str]] = set()  # (method, path) 防重复注册

    def register_ws(self, path: str, handler: Callable[[web.Request], Any]) -> None:
        if (("GET", path) in self._registered) or (("HEAD", path) in self._registered):
            logger.warning("反向驱动已存在 WS 路由 {},忽略重复注册", path)
            return
        self._app.router.add_get(path, handler)
        self._registered.add(("GET", path))

    def register_http(self, path: str, handler: Callable[[web.Request], Any], method: str = "POST") -> None:
        key = (method.upper(), path)
        if key in self._registered:
            logger.warning("反向驱动已存在路由 {} {},忽略重复注册", method, path)
            return
        self._app.router.add_route(method, path, handler)
        self._registered.add(key)

    @property
    def app(self) -> web.Application:
        return self._app

    @property
    def is_started(self) -> bool:
        return self._started

    async def start(self) -> None:
        if self._started:
            return
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, self.host, self.port)
        await self._site.start()
        self._started = True
        logger.info(f"反向驱动已启动: http://{self.host}:{self.port}")

    async def stop(self) -> None:
        if not self._started:
            return
        if self._site is not None:
            await self._site.stop()
            self._site = None
        await self._runner.cleanup()
        self._started = False
        self._registered.clear()
        logger.info("反向驱动已停止")


class ReverseServerMixin:
    """被动接收类适配器共享的服务器生命周期(driver 挂载或自持端口)。

    子类职责:
    - `__init__` 调用 `self._init_server(host, port, path, driver)`
    - 实现 `_register_driver_route(driver, path)` 与 `_register_routes(app)`
    - 路由处理函数 `_ws_handler` / `_webhook_handler`
    - 持有 `self.token`(可为空)
    """

    host: str
    port: int
    path: str
    token: str

    def _init_server(self, host: str, port: int, path: str, driver: ReverseDriver | None) -> None:
        self.host = host
        self.port = port
        self.path = path
        self._driver = driver
        self._app: web.Application | None = None
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None
        self._auth_warned = False
        if driver is not None:
            self._register_driver_route(driver, path)
        else:
            self._app = web.Application()
            self._register_routes(self._app)
            self._runner = web.AppRunner(self._app)

    def _register_driver_route(self, driver: ReverseDriver, path: str) -> None:
        raise NotImplementedError

    def _register_routes(self, app: web.Application) -> None:
        raise NotImplementedError

    async def _start_server(self, kind: str, url_prefix: str = "http") -> None:
        if self._driver is not None:
            self._warn_exposed_without_auth()
            logger.info(
                f"{kind} 路由已挂载: {url_prefix}://{self._driver.host}:{self._driver.port}{self.path}"
            )
            return
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, self.host, self.port)
        await self._site.start()
        logger.info(f"{kind} 已启动: {url_prefix}://{self.host}:{self.port}{self.path}")

    async def _stop_server(self) -> None:
        if self._driver is not None:
            return
        if self._site is not None:
            await self._site.stop()
            self._site = None
        await self._runner.cleanup()

    def _warn_exposed_without_auth(self) -> None:
        """未配置 token 且绑定非本机地址时告警(防伪造 webhook 事件)。"""
        if self.token or self._auth_warned:
            return
        host = str(self._driver.host if self._driver is not None else self.host).lower()
        if host not in LOOPBACK_HOSTS:
            self._auth_warned = True
            logger.warning(
                f"{type(self).__name__} 未配置 token 且监听非本机地址({host}),"
                "任何能访问该端口的设备都可上报伪造事件。建议设置 token 或仅绑定 127.0.0.1。"
            )

    def _authorized(self, request: web.Request) -> bool:
        """恒定时间比较 Authorization 头(未配置 token 时放行)。"""
        if not self.token:
            return True
        actual = request.headers.get("Authorization", "")
        return hmac.compare_digest(actual, f"Bearer {self.token}")
