"""超大工具结果落盘:超长输出写入 data/tool_results/,返回引用标记。

工具结果超过阈值时,全文落盘、向模型返回简短引用(避免上下文膨胀),
模型需要时可经专用工具读取。原子写盘 + 定期清理过期文件。
"""
from __future__ import annotations

import asyncio
import time
import uuid
from pathlib import Path

from loguru import logger


class ToolResultDisk:
    """工具结果磁盘存储。"""

    def __init__(self, data_dir: str = "data/tool_results", limit: int = 4000, ttl: int = 3600):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.limit = max(100, int(limit))
        self.ttl = int(ttl)

    async def store(self, text: str, tool_name: str = "tool") -> str:
        """把超长结果落盘,返回引用标记(形如 `<tool_result:<key>>`)。

        短结果原样返回;超长才落盘。
        """
        text = text or ""
        if len(text) <= self.limit:
            return text
        key = f"{tool_name}_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"
        path = self.data_dir / f"{key}.txt"
        await asyncio.to_thread(path.write_text, text, encoding="utf-8")
        logger.debug("工具结果落盘: {} ({} 字符)", path.name, len(text))
        return (
            f"<tool_result:{key}>(共 {len(text)} 字符,已存盘)"
            f"\n开头预览:\n{text[:500]}"
        )

    def read(self, key: str) -> str | None:
        """按引用 key 读取完整结果(过期文件返回 None 并清理)。"""
        path = self.data_dir / f"{key}.txt"
        if not path.is_file():
            return None
        try:
            content = path.read_text(encoding="utf-8")
        except OSError:
            return None
        self._gc()
        return content

    def _gc(self) -> None:
        """清理过期文件(防内存/磁盘泄漏)。"""
        try:
            now = time.time()
            for p in self.data_dir.glob("*.txt"):
                try:
                    if now - p.stat().st_mtime > self.ttl:
                        p.unlink(missing_ok=True)
                except OSError:
                    pass
        except OSError:
            pass
