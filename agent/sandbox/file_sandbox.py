"""文件系统沙箱:chroot 风格路径映射,限制读写都在 root 目录内(防路径穿越)。

所有路径先 resolve 到 root 下,越界一律拒绝。
"""
from __future__ import annotations

from pathlib import Path


class FileSandbox:
    """限定在 root 目录内的文件读写。"""

    def __init__(self, root: str | Path = "."):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def resolve(self, path: str | Path) -> Path:
        """把用户路径安全解析到 root 内;越界抛 ValueError。"""
        p = (self.root / path).resolve()
        if p != self.root and self.root not in p.parents:
            raise ValueError(f"路径越界(沙箱外): {path}")
        return p

    def read(self, path: str | Path) -> str:
        return self.resolve(path).read_text(encoding="utf-8", errors="replace")

    def write(self, path: str | Path, content: str) -> Path:
        p = self.resolve(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return p

    def exists(self, path: str | Path) -> bool:
        try:
            return self.resolve(path).exists()
        except ValueError:
            return False
