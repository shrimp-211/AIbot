"""指令管理(compat stub,本项目指令由插件注册表管理)。"""

from __future__ import annotations


def list_commands(*args, **kwargs):
    return []


def list_command_conflicts(*args, **kwargs):
    return []


def rename_command(*args, **kwargs):
    return None


def toggle_command(*args, **kwargs):
    return None


def update_command_permission(*args, **kwargs):
    return None
