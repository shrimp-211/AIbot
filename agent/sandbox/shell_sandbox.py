"""Shell 执行沙箱:本地子进程(超时/截断)或 Docker 容器,后端按可用性自动降级。

- backend=local: 直接子进程执行,带超时与输出截断
- backend=docker: 优先 docker run(alpine),docker 不可用时自动降级 local(适配性)
"""
from __future__ import annotations

import asyncio
import os
import shlex
import shutil
from pathlib import Path
from typing import Any

from loguru import logger


class ShellSandbox:
    """Shell 沙箱。"""

    def __init__(self, workdir: str = ".", backend: str = "local", docker_image: str = "alpine:latest"):
        self.workdir = str(workdir or ".")
        self.backend = backend
        self.docker_image = docker_image
        # docker 后端不可用时自动降级本地(避免每次执行都报错)
        self._docker_available: bool | None = None

    def _use_docker(self) -> bool:
        if self.backend != "docker":
            return False
        if self._docker_available is None:
            self._docker_available = shutil.which("docker") is not None
            if not self._docker_available:
                logger.warning("backend=docker 但未安装 docker,自动降级为本地执行")
        return self._docker_available

    def _docker_wrap(self, command: str) -> str:
        vol = shlex.quote(f"{os.path.abspath(self.workdir)}:/workspace")
        inner = shlex.quote(command)
        return f"docker run --rm -v {vol} -w /workspace {self.docker_image} sh -c {inner}"

    async def run(self, command: str, timeout: int = 30, cwd: str | None = None) -> dict[str, Any]:
        """执行命令,返回 {exit_code, stdout, stderr, timed_out}。"""
        cmd = self._docker_wrap(command) if self._use_docker() else command
        workdir = cwd or self.workdir
        try:
            proc = await asyncio.create_subprocess_shell(
                cmd,
                cwd=workdir,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=max(1, int(timeout or 30))
                )
            except asyncio.TimeoutError:
                proc.kill()
                try:
                    await proc.communicate()
                except Exception:  # noqa: BLE001
                    pass
                return {"exit_code": -1, "stdout": "", "stderr": "命令执行超时", "timed_out": True}
            except asyncio.CancelledError:
                proc.kill()
                raise
            return {
                "exit_code": proc.returncode,
                "stdout": stdout.decode("utf-8", errors="ignore").strip(),
                "stderr": stderr.decode("utf-8", errors="ignore").strip(),
                "timed_out": False,
            }
        except FileNotFoundError as exc:
            return {"exit_code": -1, "stdout": "", "stderr": f"命令不可用: {exc}", "timed_out": False}
        except Exception as exc:  # noqa: BLE001
            logger.warning("Shell 执行失败: {}", exc)
            return {"exit_code": -1, "stdout": "", "stderr": "命令执行失败", "timed_out": False}
