"""工具系统:注册所有内置工具到 ToolRegistry。"""
from __future__ import annotations

from ...security.auth import AuthManager
from .base import ToolRegistry
from .file_tools import FileEditTool, FileReadTool, FileWriteTool, GlobTool, GrepTool
from .knowledge import KnowledgeAddTool, KnowledgeSearchTool
from .memory_tools import MemoryAddTool, MemoryListTool, MemorySearchTool
from .mcp_tools import MCPServerListTool
from .network import WebFetchTool, WebSearchTool
from .plan_tools import PlanTool
from .session_tools import SessionListTool, SessionLoadTool, SessionSaveTool
from .qq_admin import (
    QqEssenceTool,
    QqGroupAnnounceTool,
    QqGroupFileListTool,
    QqKickTool,
    QqMuteTool,
    QqSetAdminTool,
)
from .qq_info import QqFriendListTool, QqGroupInfoTool, QqGroupListTool, QqStrangerInfoTool
from .qq_message import QqRecallTool, QqSendImageTool, QqSendLikeTool, QqSendVoiceTool
from .skill_tools import SkillListTool, SkillStopTool, SkillUseTool
from .subagent_tools import SubagentTool
from .system import AskUserTool, BashTool, CronTool
from .task_tools import TaskTools
from .translate import TranslateTool


def build_default_registry(auth: AuthManager) -> ToolRegistry:
    """构建包含全部内置工具的注册中心。"""
    registry = ToolRegistry(auth)
    for tool in (
        WebSearchTool(),
        WebFetchTool(),
        FileReadTool(),
        FileWriteTool(),
        FileEditTool(),
        GlobTool(),
        GrepTool(),
        BashTool(),
        CronTool(),
        AskUserTool(),
        TaskTools(),
        KnowledgeAddTool(),
        KnowledgeSearchTool(),
        QqKickTool(),
        QqMuteTool(),
        QqSetAdminTool(),
        QqEssenceTool(),
        QqGroupAnnounceTool(),
        QqGroupFileListTool(),
        QqSendImageTool(),
        QqSendVoiceTool(),
        QqRecallTool(),
        QqSendLikeTool(),
        QqGroupInfoTool(),
        QqGroupListTool(),
        QqFriendListTool(),
        QqStrangerInfoTool(),
        SkillListTool(),
        SkillUseTool(),
        SkillStopTool(),
        MemorySearchTool(),
        MemoryAddTool(),
        MemoryListTool(),
        PlanTool(),
        SubagentTool(),
        SessionSaveTool(),
        SessionLoadTool(),
        SessionListTool(),
        MCPServerListTool(),
        TranslateTool(),
    ):
        registry.register(tool)
    return registry
