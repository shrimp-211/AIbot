"""外部插件示例:演示如何编写 data/plugins/ 下的插件。

加载方式:启动时自动扫描以下两处(下划线开头文件忽略):
- src/data/plugins/   内置示例,随 git 提交
- <根目录>/data/plugins/  用户本地插件

两种写法:
1. 直接使用 @registry.command() 等装饰器注册 handler(模块顶层)。
2. 定义 `setup(registry)` 或 `register(registry)` 入口函数(推荐,支持 async)。

handler 内可通过参数注入已注册的依赖(如 Config、AuthManager、MemoryStore),
参考 src/plugins/dependency.py 的解析规则。删除本文件即移除插件,
重启或执行 `/plugin reload` 可热加载。
"""
from __future__ import annotations

import datetime

from src.adapter.event import AgentEvent
from src.utils.config import Config


def setup(registry) -> None:
    """入口函数:注册本插件的命令。"""

    @registry.command("plugin_echo")
    async def plugin_echo(event: AgentEvent):
        """echo 命令的外部插件版,演示命令注册。"""
        arg = event.state.get("command_arg") or ""
        await event.reply(f"[外部插件] {arg}" if arg else "请提供要回显的内容。")
        return None

    @registry.command("uptime")
    async def uptime(event: AgentEvent, config: Config):
        """演示注入 Config 依赖。"""
        provider = config.get("llm.provider.model", "?")
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        await event.reply(f"🟢 外部插件运行正常\n模型: {provider}\n时间: {now}")
        return None
