"""沙箱系统:Shell/Python/FileSystem/Browser 组件 + 会话资源池。

参照 AstrBot 运行时 + Codex/Gemini CLI 沙箱思路:
- 各沙箱惰性依赖,后端(docker/local)按可用性自动降级,缺失时给出明确提示
- SandboxSessionPool 按会话复用组件,LRU 淘汰,防资源泄漏
"""
from .file_sandbox import FileSandbox
from .python_sandbox import PythonSandbox
from .session_pool import SandboxSession, SandboxSessionPool
from .shell_sandbox import ShellSandbox

__all__ = [
    "FileSandbox",
    "PythonSandbox",
    "ShellSandbox",
    "SandboxSession",
    "SandboxSessionPool",
]
