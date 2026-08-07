"""插件一键安装:git clone / 本地目录 → 元数据校验 → pip 安装依赖 → 载入。

安装到用户插件目录(data/plugins/),下次启动自动加载(或调用 reload)。
"""
from __future__ import annotations

import asyncio
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from loguru import logger

from .metadata_schema import parse_metadata


class PluginInstaller:
    """插件安装器。"""

    def __init__(self, plugins_dir: str | Path = "data/plugins"):
        self.plugins_dir = Path(plugins_dir)
        self.plugins_dir.mkdir(parents=True, exist_ok=True)

    async def install(self, source: str, name: str | None = None) -> dict:
        """安装插件。source 为 git URL 或本地目录。"""
        source = str(source).strip()
        if not source:
            return {"ok": False, "error": "未提供插件来源"}
        target_name = name
        tmp: Path | None = None
        try:
            if source.startswith(("http://", "https://", "git@")):
                tmp = Path(tempfile.mkdtemp(prefix="qplg_"))
                await self._git_clone(source, tmp)
                plugin_dir = self._locate_plugin_dir(tmp)
            else:
                src = Path(source)
                if not src.is_dir():
                    return {"ok": False, "error": f"插件目录不存在: {source}"}
                plugin_dir = src
            # 元数据校验
            try:
                meta = parse_metadata(plugin_dir)
            except ValueError as exc:
                return {"ok": False, "error": str(exc)}
            target_name = target_name or meta.get("name") or plugin_dir.name
            target = self.plugins_dir / _safe_name(target_name)
            # 复制到安装目录(本地目录则复制,避免破坏源)
            if plugin_dir != target:
                if target.exists():
                    shutil.rmtree(target)
                shutil.copytree(plugin_dir, target, ignore=shutil.ignore_patterns(".git", "__pycache__"))
            # 安装依赖(校验:拒绝 pip 选项注入与非法包名)
            deps = meta.get("dependencies") or []
            if deps:
                invalid = [d for d in deps if not _is_safe_dependency(d)]
                if invalid:
                    return {"ok": False, "error": f"存在不安全的依赖项,已拒绝安装: {invalid}"}
                ok = await self._pip_install(deps)
                if not ok:
                    return {"ok": False, "error": "依赖安装失败,插件目录已就绪可手动修复"}
            return {"ok": True, "name": target_name, "dir": str(target), "deps": deps}
        except Exception as exc:  # noqa: BLE001
            logger.exception("插件安装失败")
            return {"ok": False, "error": f"安装失败: {exc}"}
        finally:
            if tmp is not None:
                shutil.rmtree(tmp, ignore_errors=True)

    @staticmethod
    def _locate_plugin_dir(root: Path) -> Path:
        """git 仓库根下的插件目录:含 metadata.yaml 的目录(或仓库根)。"""
        for candidate in (root, root / "plugin", root / "src"):
            if (candidate / "metadata.yaml").is_file() or (candidate / "plugin.json").is_file():
                return candidate
        return root

    @staticmethod
    async def _git_clone(url: str, dest: Path) -> None:
        proc = await asyncio.create_subprocess_exec(
            "git", "clone", "--depth", "1", url, str(dest),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"git clone 失败: {stderr.decode('utf-8', 'ignore')[:300]}")

    @staticmethod
    async def _pip_install(deps: list[str]) -> bool:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "pip", "install", *deps,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()
        return proc.returncode == 0


def _safe_name(name: str) -> str:
    import re

    return re.sub(r"[^A-Za-z0-9_\-]", "_", name or "plugin")[:64]


def _is_safe_dependency(dep: str) -> bool:
    """校验 pip 依赖项格式:拒绝以 `-` 开头的选项注入,仅允许包名[版本约束]。

    例:requests、requests>=2.0、requests==2.31.0、package[extra]<2 均合法;
    `--index-url ...`、`-e git+...`、`requests; os.system(...)` 拒绝。
    """
    import re

    if not isinstance(dep, str) or not dep or dep.startswith("-"):
        return False
    if any(c in dep for c in ";&|$`\n"):
        return False
    # 包名:字母数字._- 开头;允许 [extras];允许版本比较符(>=,<=,==,!=,~=,<,>)
    return bool(re.fullmatch(r"[A-Za-z0-9_.\-]+(?:\[[A-Za-z0-9_,\-]+\])?(?:(?:>=|<=|==|!=|~=|<|>)[A-Za-z0-9.\-*]+)?", dep))
