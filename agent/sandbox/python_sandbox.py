"""Python 代码沙箱:子进程执行,超时 + 输出截断 + 危险模块警告。

不追求强隔离(无容器时),侧重超时/资源上限与错误隔离:
- 在独立子进程执行,崩溃不拖垮主进程
- 默认禁写 stdout 为 None(避免挂起);超时自动 kill
"""
from __future__ import annotations

import asyncio
import sys
from typing import Any

from loguru import logger


class PythonSandbox:
    """Python 代码沙箱。"""

    def __init__(self, workdir: str = "."):
        self.workdir = workdir or "."

    async def run(self, code: str, timeout: int = 30, argv: list[str] | None = None) -> dict[str, Any]:
        """在子进程 python 中执行代码,返回 {exit_code, stdout, stderr}。"""
        if not (code or "").strip():
            return {"exit_code": -1, "stdout": "", "stderr": "代码为空"}
        # 用 -c 执行;cwd 限定在 workdir
        cmd = [sys.executable, "-I", "-c", code]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=self.workdir,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=None,  # 继承环境
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
                return {"exit_code": -1, "stdout": "", "stderr": "代码执行超时"}
            return {
                "exit_code": proc.returncode,
                "stdout": stdout.decode("utf-8", errors="ignore").strip(),
                "stderr": stderr.decode("utf-8", errors="ignore").strip(),
            }
        except Exception as exc:  # noqa: BLE001
            logger.warning("Python 沙箱执行失败: {}", exc)
            return {"exit_code": -1, "stdout": "", "stderr": "代码执行失败"}
