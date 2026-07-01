"""文件工具 — FileRead, FileWrite, Glob, Grep.

参考 Claude Code 的同名工具设计。
"""

import fnmatch
import os
import re
from pathlib import Path
from typing import Any

from .base import BaseTool


class FileReadTool(BaseTool):
    name = "file_read"
    description = "读取指定文件的内容。仅限项目 data 目录内的文件。"
    permission_level = 0

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "文件路径 (相对于 data 目录)",
                },
                "lines": {
                    "type": "integer",
                    "description": "读取行数 (不指定则读取全部)",
                },
            },
            "required": ["path"],
        }

    async def execute(self, path: str, lines: int = 0, **kwargs) -> str:
        """读取文件."""
        safe_path = self._safe_path(path)
        if not safe_path.exists():
            return f"文件不存在: {path}"
        if not safe_path.is_file():
            return f"不是文件: {path}"

        try:
            content = safe_path.read_text(encoding="utf-8")
            if lines > 0:
                content = "\n".join(content.split("\n")[:lines])
            if len(content) > 5000:
                content = content[:5000] + "\n... (已截断)"
            return content
        except Exception as e:
            return f"读取失败: {e}"

    @staticmethod
    def _safe_path(path: str) -> Path:
        """确保访问限制在 data 目录内."""
        base = Path("data").resolve()
        target = (base / path).resolve()
        if not str(target).startswith(str(base)):
            raise ValueError("路径越界")
        return target


class FileWriteTool(BaseTool):
    name = "file_write"
    description = "写入内容到文件。仅限 data 目录。会覆盖已有文件。"
    permission_level = 1

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "文件路径 (相对于 data 目录)",
                },
                "content": {
                    "type": "string",
                    "description": "要写入的内容",
                },
            },
            "required": ["path", "content"],
        }

    async def execute(self, path: str, content: str, **kwargs) -> str:
        try:
            safe_path = FileReadTool._safe_path(path)
            safe_path.parent.mkdir(parents=True, exist_ok=True)
            safe_path.write_text(content, encoding="utf-8")
            return f"已写入: {path} ({len(content)} 字符)"
        except ValueError as e:
            return str(e)
        except Exception as e:
            return f"写入失败: {e}"


class GlobTool(BaseTool):
    name = "glob"
    description = "按文件名模式搜索文件，支持通配符 (*.py, **/*.md)"
    permission_level = 0

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "glob 模式，如 '*.py' 或 '**/*.json'",
                },
                "path": {
                    "type": "string",
                    "description": "搜索目录 (默认 data)",
                },
            },
            "required": ["pattern"],
        }

    async def execute(self, pattern: str, path: str = "data", **kwargs) -> str:
        try:
            base = Path(path).resolve()
            if not base.exists():
                return f"目录不存在: {path}"

            matches = []
            for f in base.rglob(pattern):
                if f.is_file():
                    matches.append(str(f.relative_to(base)))

            if not matches:
                return f"未找到匹配 '{pattern}' 的文件"

            result = f"匹配 '{pattern}' 的文件 ({len(matches)} 个):\n"
            result += "\n".join(f"  {m}" for m in sorted(matches)[:50])
            if len(matches) > 50:
                result += f"\n  ... 还有 {len(matches) - 50} 个文件"
            return result
        except Exception as e:
            return f"搜索失败: {e}"


class GrepTool(BaseTool):
    name = "grep"
    description = "在文件中搜索匹配正则表达式的内容行"
    permission_level = 0

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "要搜索的正则表达式",
                },
                "path": {
                    "type": "string",
                    "description": "搜索路径 (文件或目录)",
                },
                "glob": {
                    "type": "string",
                    "description": "文件名过滤 (如 *.py)",
                },
            },
            "required": ["pattern", "path"],
        }

    async def execute(self, pattern: str, path: str, glob: str = "*", **kwargs) -> str:
        # ReDoS 防御: 限制正则表达式长度和复杂度
        if len(pattern) > 200:
            return f"正则表达式过长 ({len(pattern)} 字符，最大 200)"
        # 拒绝已知的灾难性回溯模式
        dangerous = ["(a+)+", "(a|aa)+", "(.*a){", "(a+)*", "(?:.*){10,}"]
        if any(d in pattern for d in dangerous):
            return "正则表达式包含潜在的危险回溯模式"

        try:
            p = Path(path)
            if not p.exists():
                return f"路径不存在: {path}"

            if p.is_file():
                files = [p]
            else:
                files = [f for f in p.rglob(glob) if f.is_file()]

            results = []
            compiled = re.compile(pattern)

            for f in files:
                try:
                    content = f.read_text(encoding="utf-8")
                except Exception:
                    continue
                linenum = 0
                for line in content.split("\n"):
                    linenum += 1
                    try:
                        if compiled.search(line):
                            results.append(f"{f}:{linenum}: {line.strip()[:200]}")
                    except RuntimeError:
                        return "正则表达式匹配超限，请简化表达式"
                    if len(results) >= 50:
                        break
                if len(results) >= 50:
                    break

            if not results:
                return f"未找到匹配 '{pattern}' 的内容"

            return "\n".join(results[:50])
        except re.error as e:
            return f"无效的正则表达式: {e}"
        except Exception as e:
            return f"搜索失败: {e}"
