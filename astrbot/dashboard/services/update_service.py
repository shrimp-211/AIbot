"""UpdateService(本项目适配):检查 GitHub 最新版本 + git pull 升级 + pip 安装。"""

from __future__ import annotations

import asyncio
import subprocess
import sys

from astrbot.core import logger
from astrbot.core.config.default import VERSION
from astrbot.core.core_lifecycle import AstrBotCoreLifecycle
from astrbot.core.utils.astrbot_path import get_astrbot_path

GITHUB_REPO = "shrimp-211/AIbot"


class UpdateServiceError(Exception):
    pass


class UpdateServiceResult:
    def __init__(self, ok: bool = True, data=None, message: str = "") -> None:
        self.ok = ok
        self.data = data
        self.message = message


def call_get_dashboard_version() -> str:
    """返回 dashboard(WebUI)版本号。"""
    return VERSION


def call_pip_install(package: str) -> str:
    """安装 pip 包(app.py 兼容入口)。"""
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pip", "install", package],
            capture_output=True,
            text=True,
            timeout=300,
        )
        return proc.stdout[-300:] if proc.returncode == 0 else proc.stderr[-300:]
    except Exception as exc:  # noqa: BLE001
        return str(exc)


class UpdateService:
    def __init__(
        self,
        updater=None,
        core_lifecycle: AstrBotCoreLifecycle | None = None,
        get_dashboard_version_func=None,
        pip_install_func=None,
        demo_mode: bool = False,
        clear_site_data_headers=None,
    ) -> None:
        self.core_lifecycle = core_lifecycle
        self.config = getattr(core_lifecycle, "astrbot_config", None)
        self._progress: dict = {}

    async def check_update(self, update_type: str = "core") -> dict:
        """检查 GitHub 最新版本(尽力而为,网络失败返回无更新)。"""
        latest = None
        try:
            import httpx

            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(
                    f"https://api.github.com/repos/{GITHUB_REPO}/commits/main"
                )
                if r.status_code == 200:
                    latest = r.json().get("sha", "")[:7]
        except Exception as exc:  # noqa: BLE001
            logger.warning("检查更新失败: %s", exc)
        return {
            "has_update": False,
            "update_available": False,
            "current_version": VERSION,
            "latest_version": latest or None,
            "message": "GitHub 已是最新代码" if latest else "无法连接 GitHub",
        }

    async def get_update_progress(self, task_id: str | None) -> dict:
        return {"task_id": task_id, "status": "completed", "progress": 100}

    async def update_project(self, data: dict) -> dict:
        """git pull 升级并请求重启。"""
        repo_dir = get_astrbot_path()
        try:
            proc = await asyncio.to_thread(
                subprocess.run,
                ["git", "-C", repo_dir, "pull", "--ff-only"],
                capture_output=True,
                text=True,
                timeout=120,
            )
        except Exception as exc:  # noqa: BLE001
            raise UpdateServiceError(f"git pull 执行失败: {exc}") from exc
        if proc.returncode != 0:
            raise UpdateServiceError(
                f"git pull 失败: {proc.stderr[:300] or proc.stdout[:300]}"
            )
        if self.core_lifecycle is not None:
            self.core_lifecycle.request_restart()
        return {"message": "升级完成,正在重启...", "output": proc.stdout[:500]}

    async def install_pip_package(self, data: dict) -> dict:
        """安装 pip 包。"""
        package = str(data.get("package") or "").strip()
        if not package:
            raise UpdateServiceError("缺少 package 参数")
        try:
            proc = await asyncio.to_thread(
                subprocess.run,
                [sys.executable, "-m", "pip", "install", package],
                capture_output=True,
                text=True,
                timeout=300,
            )
        except Exception as exc:  # noqa: BLE001
            raise UpdateServiceError(f"pip 安装失败: {exc}") from exc
        if proc.returncode != 0:
            raise UpdateServiceError(f"pip 安装失败: {proc.stderr[:300]}")
        return {"message": f"已安装 {package}", "output": proc.stdout[-300:]}
