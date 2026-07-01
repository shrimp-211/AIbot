"""万能 QQ AI Agent — 主入口."""

import asyncio
import signal
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from utils.config import Config, load_config
from utils.logger import setup_logger


# ---- 基础设施初始化 ----

def _init_infrastructure(cfg: Config) -> tuple:
    """初始化日志、存储、权限、记忆."""
    logger = setup_logger(
        level=cfg.get("logging.level", "INFO"),
        fmt=cfg.get("logging.format", "text"),
        logfile=cfg.get("logging.file", ""),
    )
    logger.info("QQ AI Agent 启动中...")

    from storage.db import init_db
    init_db(base_dir="data")

    from security.auth import AuthManager
    from agent.memory.store import MemoryStore

    return logger, AuthManager(), MemoryStore()


# ---- 工具初始化 ----

def _init_tools(cfg: Config) -> tuple:
    """初始化工具注册表并注册所有工具."""
    from agent.tools.registry import ToolRegistry
    from agent.tools.web_tools import WebSearchTool, WebFetchTool
    from agent.tools.file_tools import FileReadTool, FileWriteTool, GlobTool, GrepTool
    from agent.tools.system_tools import BashTool, CronTool, AskUserTool
    from agent.tools.task_tools import (
        TodoWriteTool, TaskCreateTool, TaskGetTool, TaskListTool, TaskUpdateTool,
    )
    from agent.tools.agent_tool import AgentTool
    from agent.tools.config_tool import ConfigTool
    from agent.tools.knowledge_tools import KnowledgeSearchTool, KnowledgeAddTool
    from agent.tools.plan_tools import EnterPlanModeTool, ExitPlanModeTool
    from agent.tools.vision_tool import VisionTool
    from agent.tools.group_file_tool import QQGroupFileListTool
    from agent.tools.persona_tool import PersonaSwitchTool, PersonaListTool
    from agent.tools.qq_tools import (
        QQGroupInfoTool, QQGroupListTool, QQKickTool, QQMuteTool,
        QQSetAdminTool, QQSendImageTool, QQSendVoiceTool, QQRecallTool,
        QQSendLikeTool, QQFriendListTool, QQEssenceTool,
        QQGroupAnnounceTool, QQStrangerInfoTool, QQSignInTool,
    )

    registry = ToolRegistry()

    # 搜索引擎配置
    search_cfg = {
        "tavily_api_key": cfg.get("web_search.tavily_api_key", ""),
        "brave_api_key": cfg.get("web_search.brave_api_key", ""),
    }
    _TOOLS = [
        WebSearchTool(config=search_cfg), WebFetchTool(), FileReadTool(), FileWriteTool(),
        GlobTool(), GrepTool(), BashTool(), CronTool(), AskUserTool(),
        TodoWriteTool(), TaskCreateTool(), TaskGetTool(), TaskListTool(),
        TaskUpdateTool(), KnowledgeSearchTool(), KnowledgeAddTool(),
        EnterPlanModeTool(), ExitPlanModeTool(),
        VisionTool(), QQGroupFileListTool(),
        PersonaSwitchTool(), PersonaListTool(),
    ]

    _QQ_TOOLS = [
        QQGroupInfoTool(), QQGroupListTool(), QQKickTool(), QQMuteTool(),
        QQSetAdminTool(), QQSendImageTool(), QQSendVoiceTool(), QQRecallTool(),
        QQSendLikeTool(), QQFriendListTool(), QQEssenceTool(),
        QQGroupAnnounceTool(), QQStrangerInfoTool(), QQSignInTool(),
    ]

    for tool in _TOOLS + _QQ_TOOLS:
        registry.register(tool)

    agent_tool = AgentTool()
    registry.register(agent_tool)

    config_tool = ConfigTool(config=cfg)
    registry.register(config_tool)

    from agent.skills.registry import SkillRegistry
    skills = SkillRegistry()
    skills.load_builtin()

    return registry, skills, agent_tool, config_tool, _QQ_TOOLS


# ---- Provider 初始化 ----

def _init_provider(cfg: Config, logger) -> tuple:
    """初始化 LLM Provider、模型路由器、感知引擎、编排器."""
    from providers import create_provider

    provider = create_provider(
        provider_type=cfg.get("provider.type", "openai"),
        model=cfg.get("provider.model", "claude-sonnet-4-6"),
        api_key=cfg.get("provider.api_key", ""),
        base_url=cfg.get("provider.base_url", ""),
        max_tokens=cfg.get("provider.max_tokens", 4096),
        temperature=cfg.get("provider.temperature", 0.7),
    )

    from agent.model_registry import ModelRouter, MODEL_SPECS
    model_router = ModelRouter()
    model_router.register_all(MODEL_SPECS)
    model_router.set_active(cfg.get("provider.model", "claude-sonnet-4-6"))

    from agent.perception import PerceptionEngine
    from agent.orchestrator import ModelOrchestrator

    perception = PerceptionEngine(provider=provider, model_router=model_router)
    orchestrator = ModelOrchestrator()
    orchestrator.add_provider(provider.model, provider)

    vision_cfg = cfg.get("provider.vision", None)
    if vision_cfg:
        vp = create_provider(
            provider_type=vision_cfg.get("type", "openai"),
            model=vision_cfg.get("model", "gpt-4o"),
            api_key=vision_cfg.get("api_key", cfg.get("provider.api_key", "")),
            base_url=vision_cfg.get("base_url", ""),
            max_tokens=vision_cfg.get("max_tokens", 4096),
            temperature=vision_cfg.get("temperature", 0.7),
        )
        orchestrator.add_provider(vp.model, vp)
        perception._vision_provider = vp
        logger.info(f"视觉模型注册: {vp.model}")

    return provider, model_router, perception, orchestrator


# ---- Agent 引擎初始化 ----

async def _init_engine(cfg: Config, provider, tools, skills, memory, auth) -> tuple:
    """初始化 Agent 引擎并注入依赖."""
    from agent.engine import AgentEngine

    engine = AgentEngine(
        provider=provider, tools=tools, skills=skills,
        memory=memory, auth=auth,
        system_prompt=cfg.get("agent.system_prompt", ""),
        max_turns=cfg.get("agent.max_turns", 15),
        compressor_enabled=cfg.get("agent.compressor_enabled", True),
        max_context_tokens=cfg.get("agent.max_context_tokens", 8000),
    )

    from plugins.registry import PluginRegistry
    plugins = PluginRegistry()
    await plugins.load_all()

    return engine, plugins


# ---- 管道和适配器初始化 ----

def _init_pipeline_and_adapter(cfg: Config, engine, plugins, auth) -> tuple:
    """初始化 QQ 适配器和消息管道 (适配器必须在管道之前创建)."""
    from adapter.onebot_v11 import OneBotV11Adapter
    from pipeline.scheduler import PipelineScheduler
    from pipeline.stages.wake_check import WakeCheckStage
    from pipeline.stages.rate_limit import RateLimitStage
    from pipeline.stages.safety import ContentSafetyStage
    from pipeline.stages.preprocess import PreProcessStage
    from pipeline.stages.process import ProcessStage
    from pipeline.stages.decorate import DecorateStage
    from pipeline.stages.respond import RespondStage

    adapter = OneBotV11Adapter(
        host=cfg.get("onebot.host", "127.0.0.1"),
        port=cfg.get("onebot.port", 6199),
        path=cfg.get("onebot.path", "/ws"),
        access_token=cfg.get("onebot.access_token", ""),
    )

    scheduler = PipelineScheduler()
    scheduler.add_stage(WakeCheckStage(auth=auth, cfg=cfg))
    scheduler.add_stage(RateLimitStage(cfg=cfg))
    scheduler.add_stage(ContentSafetyStage(cfg=cfg))
    scheduler.add_stage(PreProcessStage(cfg=cfg))
    scheduler.add_stage(ProcessStage(engine=engine, plugins=plugins, cfg=cfg))
    scheduler.add_stage(DecorateStage(cfg=cfg))
    scheduler.add_stage(RespondStage(adapter=adapter))

    async def on_message(event):
        await scheduler.execute(event)

    adapter.on_message = on_message
    return adapter, scheduler


# ---- WebUI ----

def _init_webui(cfg: Config, engine, plugins, memory) -> asyncio.Task | None:
    """可选的 WebUI 启动."""
    if not cfg.get("webui.enabled", False):
        return None

    from webui.server import start_webui
    return asyncio.create_task(start_webui(cfg, engine, plugins, memory))


# ---- 主入口 ----

async def main():
    cfg = load_config("config.yaml")
    logger, auth, memory = _init_infrastructure(cfg)
    tools, skills, agent_tool, config_tool, qq_tools = _init_tools(cfg)
    provider, model_router, perception, orchestrator = _init_provider(cfg, logger)
    engine, plugins = await _init_engine(cfg, provider, tools, skills, memory, auth)

    agent_tool.set_engine(engine)
    config_tool.set_config(cfg)
    engine.orchestrator = orchestrator
    from webui.server import _record_trace
    engine.trace_callback = _record_trace

    # 人格系统
    from agent.persona import PersonaManager
    persona_mgr = PersonaManager()

    # 注入 Vision/Plan/Persona 依赖
    for tool in tools._tools.values():
        if hasattr(tool, 'perception') and tool.perception is None:
            tool.perception = perception
        if hasattr(tool, 'engine') and tool.engine is None:
            tool.engine = engine
        if hasattr(tool, 'manager') and tool.manager is None:
            tool.manager = persona_mgr

    # MCP 服务器连接
    from agent.mcp_client import MCPManager
    mcp_manager = MCPManager()
    mcp_servers = cfg.get("mcp.servers", [])
    for srv in mcp_servers:
        if cfg.get("mcp.enabled", False):
            asyncio.create_task(mcp_manager.connect_server(
                srv.get("name", ""), srv.get("command", ""), srv.get("args", []), srv.get("env", {})
            ))

    adapter, scheduler = _init_pipeline_and_adapter(cfg, engine, plugins, auth)
    for qt in qq_tools:
        qt.adapter = adapter

    webui_task = _init_webui(cfg, engine, plugins, memory)

    # 自动记忆
    from agent.auto_memory import AutoMemory
    auto_mem = AutoMemory()

    # 生命周期管理
    stop_event = asyncio.Event()

    def shutdown(sig, frame):
        logger.info("收到退出信号，正在关闭...")
        stop_event.set()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    asyncio.create_task(adapter.run(stop_event))
    asyncio.create_task(memory.run_flush_loop(stop_event))

    host, port, path = cfg.get("onebot.host"), cfg.get("onebot.port"), cfg.get("onebot.path")
    logger.info(f"QQ AI Agent 已启动，监听 ws://{host}:{port}{path}")

    await stop_event.wait()

    await memory.flush_all()
    await adapter.stop()
    if webui_task:
        webui_task.cancel()
    logger.info("QQ AI Agent 已关闭")


if __name__ == "__main__":
    asyncio.run(main())
