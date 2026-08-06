"""文件工具:读写文件、glob 匹配、grep 搜索(含安全校验)。"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ...security.auth import Decision
from .base import Tool, ToolContext

_MAX_GREP_PATTERN_LEN = 200
# 拒绝危险回溯:长交替、量词范围、嵌套重复分组 `(a+)+`、环视滥用等
_DANGEROUS_REGEX = re.compile(
    r"\((?:[^()]*\|){2,}[^()]*\)"
    r"|\{\d{2,},\d*\}"
    r"|\([^()]*[+*][^()]*\)[*+]{1,2}"
    r"|(?:a+){3,}"
)


def _resolve_path(root: str, path: str) -> Path:
    p = Path(path).expanduser()
    if not p.is_absolute():
        p = Path(root) / p
    return p.resolve()


def _check_file_access(auth, role_level: int, fp: Path, path: str) -> None:
    """先规范化到绝对路径再走权限规则,同时强制可信目录(沙箱)。

    `fp` 为解析后的绝对路径(规则匹配防 `./.env` 前缀绕过),
    `path` 为原始用户输入(用于报错提示)。
    """
    if not auth.is_path_trusted(str(fp)):
        raise PermissionError(f"路径不在可信目录内: {path}")
    decision = auth.check_path(str(fp), role_level)
    if decision == Decision.DENY:
        raise PermissionError(f"路径访问被拒绝: {path}")
    if decision == Decision.ASK and role_level < 7:
        raise PermissionError(f"路径访问需要管理员授权: {path}")


class FileReadTool(Tool):
    name = "file_read"
    description = "读取文本文件内容,可指定行范围"
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "文件路径(相对或绝对)"},
            "offset": {"type": "integer", "description": "起始行号(从0开始,默认0)"},
            "limit": {"type": "integer", "description": "读取行数(默认全部)"},
        },
        "required": ["path"],
    }

    async def execute(self, ctx: ToolContext, path: str, offset: int = 0, limit: int | None = None) -> Any:
        fp = _resolve_path(ctx.config.get("agent.workdir", "."), path)
        role = ctx.auth.get_role_level(ctx.event.user_id, ctx.event.group_id)
        _check_file_access(ctx.auth, role, fp, path)
        if not fp.exists() or not fp.is_file():
            return {"error": f"文件不存在: {path}"}
        try:
            content = await ctx.extra["loop"].run_in_executor(None, fp.read_text, "utf-8")
        except (UnicodeDecodeError, OSError) as exc:
            return {"error": f"读取失败: {exc}"}
        lines = content.splitlines()
        start = max(0, int(offset or 0))
        end = len(lines) if limit is None else min(len(lines), start + int(limit))
        body = "\n".join(lines[start:end])
        total = len(lines)
        return {"path": str(fp), "total_lines": total, "start": start, "content": body}


class FileWriteTool(Tool):
    name = "file_write"
    description = "写入内容到文件(会覆盖已有内容)"
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "文件路径"},
            "content": {"type": "string", "description": "要写入的内容"},
        },
        "required": ["path", "content"],
    }

    async def execute(self, ctx: ToolContext, path: str, content: str) -> Any:
        fp = _resolve_path(ctx.config.get("agent.workdir", "."), path)
        role = ctx.auth.get_role_level(ctx.event.user_id, ctx.event.group_id)
        _check_file_access(ctx.auth, role, fp, path)
        try:
            loop = ctx.extra["loop"]
            await loop.run_in_executor(None, fp.parent.mkdir, True, True)

            def _write() -> None:
                with open(fp, "w", encoding="utf-8") as f:
                    f.write(content)

            await loop.run_in_executor(None, _write)
        except OSError as exc:
            return {"error": f"写入失败: {exc}"}
        return {"path": str(fp), "bytes": len(content.encode("utf-8")), "ok": True}


class FileEditTool(Tool):
    name = "file_edit"
    description = "在文件中精确查找并替换文本(每次一处,old_string 必须唯一匹配)"
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "文件路径"},
            "old_string": {"type": "string", "description": "要查找的原文,需在文件中唯一出现"},
            "new_string": {"type": "string", "description": "替换为的新文本"},
        },
        "required": ["path", "old_string", "new_string"],
    }

    async def execute(self, ctx: ToolContext, path: str, old_string: str, new_string: str) -> Any:
        if not old_string:
            return {"error": "old_string 不能为空"}
        if len(old_string) > 100_000 or len(new_string) > 100_000:
            return {"error": "编辑内容过长(>100000字符)"}
        fp = _resolve_path(ctx.config.get("agent.workdir", "."), path)
        role = ctx.auth.get_role_level(ctx.event.user_id, ctx.event.group_id)
        _check_file_access(ctx.auth, role, fp, path)
        if not fp.exists() or not fp.is_file():
            return {"error": f"文件不存在: {path}"}
        loop = ctx.extra["loop"]
        try:
            content = await loop.run_in_executor(None, fp.read_text, "utf-8")
        except (UnicodeDecodeError, OSError) as exc:
            return {"error": f"读取失败: {exc}"}
        count = content.count(old_string)
        if count == 0:
            return {"error": "未找到要替换的文本,请先用 file_read 核对文件内容"}
        if count > 1:
            return {"error": f"old_string 出现 {count} 次,请提供更长的上下文使其唯一匹配"}
        new_content = content.replace(old_string, new_string)

        def _write() -> None:
            with open(fp, "w", encoding="utf-8") as f:
                f.write(new_content)

        try:
            await loop.run_in_executor(None, _write)
        except OSError as exc:
            return {"error": f"写入失败: {exc}"}
        return {
            "path": str(fp),
            "ok": True,
            "replaced": count,
            "bytes": len(new_content.encode("utf-8")),
            "preview": new_string[:200],
        }


class GlobTool(Tool):
    name = "glob"
    description = "按 glob 模式查找文件路径"
    parameters = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "glob 模式,如 '**/*.py'"},
            "path": {"type": "string", "description": "搜索根目录(默认工作目录)"},
        },
        "required": ["pattern"],
    }

    async def execute(self, ctx: ToolContext, pattern: str, path: str | None = None) -> Any:
        root = _resolve_path(ctx.config.get("agent.workdir", "."), path or ".")
        role = ctx.auth.get_role_level(ctx.event.user_id, ctx.event.group_id)
        _check_file_access(ctx.auth, role, root, path or ".")
        if not root.exists() or not root.is_dir():
            return {"error": f"目录不存在: {path or '.'}"}
        loop = ctx.extra["loop"]

        def _glob() -> list[str]:
            try:
                matches = sorted(str(p) for p in root.glob(pattern))
            except Exception:  # noqa: BLE001
                return []
            return matches

        try:
            matches = await loop.run_in_executor(None, _glob)
        except Exception as exc:  # noqa: BLE001
            return {"error": f"glob 失败: {exc}"}
        return {"count": len(matches), "matches": matches[:100]}


class GrepTool(Tool):
    name = "grep"
    description = "在文件中按正则表达式搜索匹配行(内置 ReDoS 防护)"
    parameters = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "正则表达式(长度≤200)"},
            "path": {"type": "string", "description": "要搜索的文件路径"},
            "glob": {"type": "string", "description": "在目录中按 glob 过滤文件"},
        },
        "required": ["pattern", "path"],
    }

    async def execute(self, ctx: ToolContext, pattern: str, path: str, glob: str | None = None) -> Any:
        if len(pattern) > _MAX_GREP_PATTERN_LEN:
            return {"error": "正则表达式过长(>200字符)"}
        if _DANGEROUS_REGEX.search(pattern):
            return {"error": "正则包含危险回溯模式,已拒绝"}
        fp = _resolve_path(ctx.config.get("agent.workdir", "."), path)
        role = ctx.auth.get_role_level(ctx.event.user_id, ctx.event.group_id)
        _check_file_access(ctx.auth, role, fp, path)

        try:
            compiled = re.compile(pattern)
        except re.error as exc:
            return {"error": f"正则编译失败: {exc}"}

        targets: list[Path] = []
        loop = ctx.extra["loop"]

        def _list_targets() -> list[Path]:
            out: list[Path] = []
            if fp.is_dir():
                iterator = fp.glob(glob) if glob else fp.glob("**/*")
                out = [p for p in iterator if p.is_file()]
            elif fp.is_file():
                out = [fp]
            return out

        targets = await loop.run_in_executor(None, _list_targets)
        if not targets:
            return {"error": f"路径不存在: {path}"}

        results = []

        def _search(p: Path) -> list[str]:
            found = []
            try:
                with open(p, "r", encoding="utf-8", errors="ignore") as f:
                    for i, line in enumerate(f, 1):
                        if compiled.search(line.rstrip("\n")):
                            found.append(f"{p}:{i}:{line.rstrip()[:200]}")
                            if len(found) >= 50:
                                break
            except OSError:
                pass
            return found

        for t in targets[:50]:
            found = await loop.run_in_executor(None, _search, t)
            results.extend(found)
            if len(results) >= 100:
                break

        return {"count": len(results), "matches": results}
