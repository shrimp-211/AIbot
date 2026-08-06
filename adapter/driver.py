"""反向驱动(Reverse Driver):托管所有被动接收连接的服务端。

参考 NoneBot2 Driver 抽象:单个 aiohttp 应用承载多个适配器的
WebSocket / HTTP 路由,避免每个适配器各自开端口、互不感知。

- register_ws(path, handler):注册 WebSocket 服务端路由(GET)
- register_http(path, handler, method):注册 HTTP webhook 路由
- start()/stop():统一启动/关闭(idempotent)

主动发起连接的适配器(正向 WS 客户端、Telegram 长轮询)不依赖本驱动。
"""
from __future__ import annotations

from typing import Any, Callable

from aiohttp import web
from loguru import logger


class ReverseDriver:
    """被动接收类适配器共享的 HTTP/WS 服务端。"""

    def __init__(self, host: str = "127.0.0.1", port: int = 6199):
        self.host = host
        self.port = port
        self._app = web.Application()
        self._runner = web.AppRunner(self._app)
        self._site: web.TCPSite | None = None
        self._started = False

    def register_ws(self, path: str, handler: Callable[[web.Request], Any]) -> None:
        self._app.router.add_get(path, handler)

    def register_http(self, path: str, handler: Callable[[web.Request], Any], method: str = "POST") -> None:
        self._app.router.add_route(method, path, handler)

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
        logger.info("反向驱动已停止")
