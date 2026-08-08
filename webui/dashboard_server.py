"""AstrBot Dashboard 服务集成。

用 uvicorn 在现有 event loop 中运行 AstrBot 的 WebUI(dashboard):
- 把本项目配置绑定到 compat 的 ``AstrBotConfig``
- 创建 ``AstrBotCoreLifecycle`` 并绑定本项目核心服务
- 实例化 ``astrbot.dashboard.server.AstrBotDashboard`` 组装 ASGI 应用
- 伺服前端构建产物 ``webui/dashboard/dist``

main.py 中 ``webui.enabled`` 时启动本服务,替代旧的 aiohttp WebUIServer。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from loguru import logger

ROOT_DIR = Path(__file__).resolve().parent.parent.parent  # 项目根 QQbot
DASHBOARD_DIST = Path(__file__).resolve().parent / "dashboard" / "dist"


class DashboardServer:
    """AstrBot Dashboard 服务(uvicorn 驱动)。"""

    def __init__(
        self,
        config,
        host: str = "127.0.0.1",
        port: int = 8080,
        deps: dict | None = None,
        config_path: str | None = None,
    ) -> None:
        self._config = config
        self._host = host
        self._port = port
        self._deps = deps or {}
        self._config_path = config_path
        self._lifecycle = None
        self._server = None
        self._serve_task: asyncio.Task | None = None

    async def start(self) -> None:
        # 1. 绑定本项目配置到 compat AstrBotConfig
        from astrbot.core.config.astrbot_config import AstrBotConfig

        AstrBotConfig.bind_project_config(self._config, self._config_path)

        # 2. 创建生命周期门面并绑定本项目服务
        from astrbot.core.core_lifecycle import AstrBotCoreLifecycle

        life = AstrBotCoreLifecycle()
        life.bind_project(
            config=self._config,
            config_path=self._config_path,
            engine=self._deps.get("engine"),
            tools=self._deps.get("tools"),
            provider=self._deps.get("provider"),
            persona=self._deps.get("persona"),
            cron=self._deps.get("cron"),
            knowledge=self._deps.get("knowledge"),
            plugin_registry=self._deps.get("plugin_registry"),
            plugin_installer=self._deps.get("plugin_installer"),
            plugin_dirs=self._deps.get("plugin_dirs"),
            adapter_registry=self._deps.get("adapter_registry"),
        )
        self._lifecycle = life
        try:
            await life.initialize()
        except Exception as exc:  # noqa: BLE001
            logger.warning("dashboard 数据库初始化失败(降级运行): {}", exc)
        try:
            await life.start()
        except Exception as exc:  # noqa: BLE001
            logger.warning("dashboard 启动阶段失败(降级运行): {}", exc)

        # 3. 组装 AstrBotDashboard ASGI 应用
        from astrbot.dashboard.server import AstrBotDashboard

        dist = DASHBOARD_DIST if (DASHBOARD_DIST / "index.html").exists() else None
        dash = AstrBotDashboard(
            life,
            life.db,
            life.dashboard_shutdown_event,
            webui_dir=str(dist) if dist is not None else None,
        )
        self._dash = dash
        app = dash.asgi_app

        # 4. uvicorn 在当前 event loop 中运行
        import uvicorn

        uconfig = uvicorn.Config(
            app,
            host=self._host,
            port=self._port,
            log_level="warning",
            access_log=False,
        )
        self._server = uvicorn.Server(uconfig)
        self._serve_task = asyncio.create_task(self._server.serve())
        # 等待绑定完成
        for _ in range(100):
            if getattr(self._server, "started", False):
                break
            if self._serve_task.done():
                break
            await asyncio.sleep(0.05)
        logger.info(
            "🌐 WebUI(AstrBot Dashboard)已启动: http://{}:{}{}",
            self._host,
            self._port,
            " (前端 dist 缺失,仅 API)" if dist is None else "",
        )

    async def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._serve_task is not None:
            try:
                await asyncio.wait_for(self._serve_task, timeout=5)
            except (asyncio.TimeoutError, Exception):  # noqa: BLE001
                self._serve_task.cancel()
        if self._lifecycle is not None:
            await self._lifecycle.stop()
        logger.info("WebUI 已关闭")
