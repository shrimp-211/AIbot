"""服务器状态:显示系统 CPU/内存/运行时间。优先 psutil,无则 fallback。

命令:/状态
"""
from __future__ import annotations

import asyncio
import os
import platform
import time

from src.adapter.event import AgentEvent

_start_time = time.time()


def _fmt_uptime(seconds: float) -> str:
    d, r = divmod(int(seconds), 86400)
    h, r = divmod(r, 3600)
    m, s = divmod(r, 60)
    parts = []
    if d:
        parts.append(f"{d}天")
    if h:
        parts.append(f"{h}时")
    if m:
        parts.append(f"{m}分")
    parts.append(f"{s}秒")
    return "".join(parts)


def _cpu_info() -> str:
    try:
        import psutil
        pct = psutil.cpu_percent(interval=0.1)
        cnt = psutil.cpu_count()
        return f"{cnt}核 {pct:.1f}%"
    except ImportError:
        pass
    cnt = os.cpu_count()
    if platform.system() == "Linux":
        try:
            with open("/proc/cpuinfo") as f:
                model = ""
                cores = 0
                for line in f:
                    if line.startswith("model name"):
                        model = line.split(":")[1].strip()
                    if line.startswith("processor"):
                        cores += 1
                return f"{cores}核 {model}" if model else f"{cores}核"
        except OSError:
            pass
    return f"{cnt}核" if cnt else "未知"


def _mem_info() -> str:
    try:
        import psutil
        m = psutil.virtual_memory()
        return f"{m.used/(1024**3):.1f}G/{m.total/(1024**3):.1f}G ({m.percent:.1f}%)"
    except ImportError:
        pass
    if platform.system() == "Linux":
        try:
            with open("/proc/meminfo") as f:
                info = {}
                for line in f:
                    k, *v = line.split(":")
                    if v:
                        info[k.strip()] = v[0].strip()
            total = int(info.get("MemTotal", "0").split()[0]) / 1024
            avail = int(info.get("MemAvailable", "0").split()[0]) / 1024 if "MemAvailable" in info else total - int(info.get("MemFree", "0").split()[0]) / 1024
            used = total - avail
            return f"{used:.1f}M/{total:.1f}M ({used/max(total,1)*100:.1f}%)"
        except (OSError, ValueError, KeyError, IndexError):
            pass
    return "无法获取"


def _uptime_str() -> str:
    try:
        import psutil
        return _fmt_uptime(time.time() - psutil.boot_time())
    except ImportError:
        pass
    if platform.system() == "Linux":
        try:
            with open("/proc/uptime") as f:
                return _fmt_uptime(float(f.read().split()[0]))
        except (OSError, ValueError):
            pass
    return _fmt_uptime(time.time() - _start_time)


def setup(registry) -> None:
    @registry.command("状态")
    async def handler(event: AgentEvent):
        # psutil.cpu_percent 与 /proc 读取均为阻塞 I/O,移到线程避免阻塞事件循环
        cpu = await asyncio.to_thread(_cpu_info)
        mem = await asyncio.to_thread(_mem_info)
        up = await asyncio.to_thread(_uptime_str)
        text = (
            f"系统: {platform.system()} | 主机: {platform.node() or '未知'}\n"
            f"Python: {platform.python_version()}\n"
            f"CPU: {cpu}\n"
            f"内存: {mem}\n"
            f"运行: {up}"
        )
        await event.reply(text)
        return None
