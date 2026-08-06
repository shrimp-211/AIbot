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
from typing import Callable

from loguru import logger

from .adapter import (
    AdapterRegistry,
    AgentEvent,
    BaseAdapter,
    OneBotV11Adapter,
    OneBotV11Client,
    OneBotV11Http,
    QQOfficialAdapter,
    TelegramAdapter,
)
from .agent.engine import AgentEngine
from .agent.hooks import HookManager
from .agent.mcp import MCPManager
from .agent.memory.files import FileMemoryStore
from .agent.memory.sqlite_store import SQLiteStore
from .agent.memory.store import MemoryStore
from .agent.persona import PersonaManager
from .agent.proactive import CronManager
from .agent.skills import SkillRegistry
from .agent.tools import build_default_registry
from .agent.tools.mcp_tools import MCPTool
from .pipeline.scheduler import PipelineScheduler
from .pipeline.stages import (
    ContentSafetyStage,
    DecorateStage,
    NoticeStage,
    PreProcessStage,
    ProcessStage,
    RateLimitStage,
    RespondStage,
    WakeCheckStage,
)
from .plugins.registry import PluginRegistry
from .providers.base import create_provider
from .security.audit import AuditLogger
from .security.auth import AuthManager
from .storage.db import JsonKV
from .utils.config import Config, load_config
from .utils.logger import setup_logger
from .webui.server import WebUIServer

BASE_DIR = Path(__file__).resolve().parent
ROOT_DIR = BASE_DIR.parent
DEFAULT_CONFIG = BASE_DIR / "config.yaml"


def _plugin_dirs() -> list[Path]:
    """外部插件扫描目录:内置示例(src/data/plugins,随 git) + 用户本地(根 data/plugins)。"""
    return [BASE_DIR / "data" / "plugins", ROOT_DIR / "data" / "plugins"]


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

    @registry.command("approve", permission_level=7)
    async def approve(event: AgentEvent):
        code = (event.state.get("command_arg") or "").strip()
        if not code:
            await event.reply("用法: /approve <配对码>")
            return None
        result = auth.approve_pairing(code, event.user_id)
        await event.reply(f"配对结果: {result}")
        return None

    @registry.command("plugins", permission_level=1)
    async def list_plugins(event: AgentEvent):
        names: set[str] = set()
        for d in _plugin_dirs():
            if d.is_dir():
                names.update(p.stem for p in d.glob("*.py") if not p.name.startswith("_"))
        lines = [f"📦 外部插件: {len(names)} 个"]
        lines.extend(f"· {n}" for n in sorted(names))
        lines.append(f"内置 handler: {registry.handler_count()} 个")
        await event.reply("\n".join(lines))
        return None

    @registry.command("plugin reload", permission_level=7)
    async def reload_plugins(event: AgentEvent):
        registry.unload_external()
        loaded: list[str] = []
        for d in _plugin_dirs():
            loaded.extend(await registry.load_from_directory(d))
        await event.reply(f"插件重载完成: {loaded or '无外部插件'}")
        return None


def build_pipeline(
    config: Config,
    auth: AuthManager,
    plugin_registry: PluginRegistry,
    engine: AgentEngine,
    db: JsonKV | None = None,
    adapter_getter: Callable[[], BaseAdapter | None] | None = None,
) -> PipelineScheduler:
    """构建洋葱模型管道。NoticeStage 置于最前,先消费 notice/request 事件。"""
    return PipelineScheduler(
        [
            NoticeStage(config, db=db, adapter_getter=adapter_getter),
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
        trusted_folders=list(config.get("security.trusted_folders", []) or []),
        sandbox_enabled=bool(config.get("security.sandbox_enabled", False)),
    )
    auth.load_dict(auth_db.get("auth", {}))

    # 审计日志
    audit_logger = AuditLogger(
        ROOT_DIR / "data" / "audit.jsonl",
        enabled=bool(config.get("security.audit_enabled", True)),
    )

    # 工具
    tools = build_default_registry(auth)

    # MCP 外部工具(按配置启动服务器并动态注册)
    mcp_manager = MCPManager()
    mcp_servers = config.get("mcp.servers", []) or []
    if mcp_servers:
        await mcp_manager.start_servers(mcp_servers)
        for server in mcp_manager.list_servers():
            for schema in server.tools:
                tools.register(MCPTool(server.name, schema, permission_level=server.permission_level))
        logger.info(f"MCP 已接入 {len(mcp_manager.list_servers())} 个服务器")

    # Provider
    provider_cfg = config.get("llm.provider", {})
    provider = create_provider(provider_cfg)
    logger.info(f"LLM Provider: {type(provider).__name__} | 模型: {provider_cfg.get('model', '')}")

    # 记忆 / 人格 / 钩子 / 定时任务 / 技能
    memory = MemoryStore(db)
    sqlite_store = SQLiteStore(ROOT_DIR / "data" / "memory.sqlite3")
    file_memory = FileMemoryStore(ROOT_DIR / "data" / "memory")
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
        sqlite_store=sqlite_store,
        file_memory=file_memory,
        mcp_manager=mcp_manager,
        audit_logger=audit_logger,
    )

    # 插件注册中心
    plugin_registry = PluginRegistry(auth)
    plugin_registry.register_dependency(Config, config)
    plugin_registry.register_dependency(AuthManager, auth)
    plugin_registry.register_dependency(MemoryStore, memory)
    plugin_registry.register_dependency(JsonKV, db)  # 插件持久化数据
    register_builtin_plugins(plugin_registry, config, auth)

    # 外部插件(热加载 */data/plugins/*.py,支持 setup(registry) 入口)
    for plugin_dir in _plugin_dirs():
        await plugin_registry.load_from_directory(plugin_dir)

    # 适配器注册中心(NoticeStage 经 adapter_getter 惰性获取主适配器)
    adapter_registry = AdapterRegistry()

    # 管道
    pipeline = build_pipeline(
        config, auth, plugin_registry, engine,
        db=db,
        adapter_getter=lambda: adapter_registry.get("qq"),
    )
    adapter_registry.set_callback(pipeline.execute)

    main_adapter = OneBotV11Adapter(
        host=config.get("onebot.host", "127.0.0.1"),
        port=int(config.get("onebot.port", 6199)),
        path=config.get("onebot.path", "/ws"),
        token=config.get("onebot.token", ""),
        self_id=config.get("onebot.self_id", ""),
    )
    adapter_registry.register("qq", main_adapter)

    if config.get("onebot_forward.enabled", False):
        adapter_registry.register(
            "qq_forward",
            OneBotV11Client(
                url=config.get("onebot_forward.url", ""),
                token=config.get("onebot_forward.token", ""),
                self_id=config.get("onebot_forward.self_id", ""),
            ),
        )

    if config.get("onebot_http.enabled", False):
        adapter_registry.register(
            "qq_http",
            OneBotV11Http(
                host=config.get("onebot_http.host", "127.0.0.1"),
                port=int(config.get("onebot_http.port", 6198)),
                path=config.get("onebot_http.path", "/onebot"),
                http_url=config.get("onebot_http.http_url", ""),
                token=config.get("onebot_http.token", ""),
                self_id=config.get("onebot_http.self_id", ""),
            ),
        )

    if config.get("qq_official.enabled", False):
        adapter_registry.register(
            "qq_official",
            QQOfficialAdapter(
                host=config.get("qq_official.host", "127.0.0.1"),
                port=int(config.get("qq_official.port", 6197)),
                path=config.get("qq_official.path", "/qq-official"),
                app_id=config.get("qq_official.app_id", ""),
                app_secret=config.get("qq_official.app_secret", ""),
                sign_secret=config.get("qq_official.sign_secret", ""),
            ),
        )

    if config.get("telegram.enabled", False):
        adapter_registry.register(
            "telegram",
            TelegramAdapter(
                token=config.get("telegram.token", ""),
                allowed_chat_ids=list(config.get("telegram.allowed_chat_ids", []) or []),
                poll_timeout=float(config.get("telegram.poll_timeout", 30) or 30),
            ),
        )

    # 回填 adapter:主 QQ 适配器作为默认发送通道
    engine.adapter = main_adapter
    cron._adapter = main_adapter

    # 启动服务
    await cron.start()
    await adapter_registry.start_all()

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
    await adapter_registry.stop_all()
    await cron.stop()
    await sqlite_store.close()
    await mcp_manager.stop_all()
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
