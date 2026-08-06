"""QQ AI Agent 入口:分步初始化 + 生命周期管理。

启动流程:基础设施(日志/存储/权限/记忆) → 工具注册 → Provider →
Agent 引擎 + 插件 → 管道 + OneBot 适配器 → Cron/WebUI → 信号监听。
"""
from __future__ import annotations

import argparse
import asyncio
import signal
import sys
from pathlib import Path

from loguru import logger

from .adapter.event import AgentEvent
from .adapter.onebot_v11 import OneBotV11Adapter
from .agent.engine import AgentEngine
from .agent.hooks import HookManager
from .agent.memory.store import MemoryStore
from .agent.persona import PersonaManager
from .agent.proactive import CronManager
from .agent.skills import SkillRegistry
from .agent.tools import build_default_registry
from .pipeline.scheduler import PipelineScheduler
from .pipeline.stages import (
    ContentSafetyStage,
    DecorateStage,
    PreProcessStage,
    ProcessStage,
    RateLimitStage,
    RespondStage,
    WakeCheckStage,
)
from .plugins.registry import PluginRegistry
from .providers.base import create_provider
from .security.auth import AuthManager
from .storage.db import JsonKV
from .utils.config import Config, load_config
from .utils.logger import setup_logger
from .webui.server import WebUIServer

BASE_DIR = Path(__file__).resolve().parent
ROOT_DIR = BASE_DIR.parent
DEFAULT_CONFIG = BASE_DIR / "config.yaml"


def make_send_reply(adapter: OneBotV11Adapter):
    """构建 event.reply 的发送回调。"""

    async def send_reply(event: AgentEvent, text: str, at: bool = False) -> None:
        if not text:
            return
        content = (
            f"[CQ:at,qq={event.user_id}] {text}"
            if at and event.message_type == "group"
            else text
        )
        if event.message_type == "group" and event.group_id:
            await adapter.send_group_msg(event.group_id, content)
        else:
            await adapter.send_private_msg(event.user_id, content)

    return send_reply


def register_builtin_plugins(registry: PluginRegistry, config: Config, auth: AuthManager) -> None:
    """注册内置演示插件(可被 data/plugins 的外部插件补充)。"""

    @registry.command("ping")
    async def ping(event: AgentEvent):
        await event.reply("pong 🏓")
        return None

    @registry.command("echo")
    async def echo(event: AgentEvent):
        arg = event.state.get("command_arg", "")
        if arg:
            await event.reply(arg)
        return None

    @registry.command("whoami", permission_level=1)
    async def whoami(event: AgentEvent):
        role = auth.get_role_level(event.user_id, event.group_id)
        await event.reply(
            f"你是 {event.sender_name}({event.user_id})\n"
            f"角色等级: {role}\n"
            f"会话: {event.session_id}"
        )
        return None


def build_pipeline(config: Config, auth: AuthManager, plugin_registry: PluginRegistry, engine: AgentEngine) -> PipelineScheduler:
    return PipelineScheduler(
        [
            WakeCheckStage(config, auth),
            RateLimitStage(config),
            ContentSafetyStage(config),
            PreProcessStage(),
            ProcessStage(plugin_registry, engine),
            DecorateStage(),
            RespondStage(),
        ]
    )


async def run(config_path: str | Path = DEFAULT_CONFIG, log_level: str = "INFO") -> None:
    config = load_config(config_path)
    setup_logger(level=log_level, log_dir=ROOT_DIR / "data" / "logs")
    logger.info(f"加载配置: {config_path}")

    # 存储
    db = JsonKV(ROOT_DIR / "data" / "agent.json")
    auth_db = JsonKV(ROOT_DIR / "data" / "auth.json")
    db.start()
    auth_db.start()

    # 权限
    auth = AuthManager(
        admin_users=tuple(config.get("security.admin_users", []) or []),
        super_admin_users=tuple(config.get("security.super_admin_users", []) or []),
    )
    auth.load_dict(auth_db.get("auth", {}))

    # 工具
    tools = build_default_registry(auth)

    # Provider
    provider_cfg = config.get("llm.provider", {})
    provider = create_provider(provider_cfg)
    logger.info(f"LLM Provider: {type(provider).__name__} | 模型: {provider_cfg.get('model', '')}")

    # 记忆 / 人格 / 钩子 / 定时任务 / 技能
    memory = MemoryStore(db)
    persona = PersonaManager(db)
    hooks = HookManager()
    cron = CronManager(adapter=None, config=config, db=db)
    skills = SkillRegistry()
    skills.reload(str(BASE_DIR / "skills"), str(ROOT_DIR / "data" / "skills"))
    logger.info(f"技能已加载: {len(skills.all())} 个")

    # Agent 引擎
    engine = AgentEngine(
        provider=provider,
        tools=tools,
        memory=memory,
        auth=auth,
        config=config,
        adapter=None,
        db=db,
        persona_manager=persona,
        hooks=hooks,
        cron_manager=cron,
        skills=skills,
    )

    # 插件注册中心
    plugin_registry = PluginRegistry(auth)
    plugin_registry.register_dependency(Config, config)
    plugin_registry.register_dependency(AuthManager, auth)
    plugin_registry.register_dependency(MemoryStore, memory)
    register_builtin_plugins(plugin_registry, config, auth)

    # 管道
    pipeline = build_pipeline(config, auth, plugin_registry, engine)

    # OneBot 适配器
    adapter = OneBotV11Adapter(
        host=config.get("onebot.host", "127.0.0.1"),
        port=int(config.get("onebot.port", 6199)),
        path=config.get("onebot.path", "/ws"),
        token=config.get("onebot.token", ""),
        self_id=config.get("onebot.self_id", ""),
    )
    adapter.on_event = pipeline.execute
    adapter.send_callback = make_send_reply(adapter)

    # 回填 adapter
    engine.adapter = adapter
    cron._adapter = adapter

    # 启动服务
    await cron.start()
    await adapter.start()

    webui: WebUIServer | None = None
    if config.get("webui.enabled", True):
        webui = WebUIServer(
            config,
            host=config.get("webui.host", "127.0.0.1"),
            port=int(config.get("webui.port", 8080)),
            deps={"engine": engine, "tools": tools, "cron": cron},
        )
        await webui.start()

    logger.info(
        f"✅ QQ AI Agent 已启动 | 工具: {len(tools.names())} | "
        f"管道阶段: {len(pipeline.stages)} | 插件 handler: {plugin_registry.handler_count()}"
    )

    # 等待停止信号
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            pass

    await stop_event.wait()
    logger.info("收到停止信号,正在关闭...")

    # 关闭
    await adapter.stop()
    await cron.stop()
    if webui is not None:
        await webui.stop()
    await db.stop()
    await auth_db.stop()
    logger.info("👋 QQ AI Agent 已停止")


def main() -> None:
    parser = argparse.ArgumentParser(description="QQ AI Agent")
    parser.add_argument("-c", "--config", default=str(DEFAULT_CONFIG), help="配置文件路径")
    parser.add_argument("--log-level", default="INFO", help="日志级别 DEBUG/INFO/WARNING")
    args = parser.parse_args()
    try:
        asyncio.run(run(config_path=args.config, log_level=args.log_level))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
