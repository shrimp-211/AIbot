"""指令管理(compat stub,本项目指令由插件注册表管理)。"""

from __future__ import annotations


async def list_commands(*args, **kwargs):
    return []


async def list_command_conflicts(*args, **kwargs):
    return []


async def rename_command(*args, **kwargs):
    return None


async def toggle_command(*args, **kwargs):
    return None


async def update_command_permission(*args, **kwargs):
    return None
