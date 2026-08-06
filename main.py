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

from .adapter import AdapterRegistry, AgentEvent, BaseAdapter, ReverseDriver
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
    PluginStage,
    PreProcessStage,
    ProcessStage,
    RateLimitStage,
    RespondStage,
    SecurityStage,
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


def _adapter_dirs() -> list[Path]:
    """适配器插件扫描目录:内置(src/data/adapters) + 用户自定义(根 data/adapters)。"""
    return [BASE_DIR / "data" / "adapters", ROOT_DIR / "data" / "adapters"]


def load_adapters(
    adapter_registry: AdapterRegistry,
    config: Config,
    driver: ReverseDriver | None = None,
) -> list[tuple[str, BaseAdapter]]:
    """从 data/adapters/ 加载平台适配器插件(非侵入式,无需修改 main.py)。

    每个 .py 模块提供 `register(adapter_registry, config, driver)` 入口,
    返回 BaseAdapter 或 None(未启用)。新增平台 = 新增一个插件文件。
    """
    import importlib.util
    import sys

    loaded: list[tuple[str, BaseAdapter]] = []
    for directory in _adapter_dirs():
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.py")):
            if path.name.startswith("_"):
                continue
            module_name = f"qqbot_adapter_{path.stem}"
            sys.modules.pop(module_name, None)  # 清理缓存以支持热加载
            try:
                spec = importlib.util.spec_from_file_location(module_name, path)
                if spec is None or spec.loader is None:
                    logger.warning("适配器插件无法解析: %s", path.name)
                    continue
                module = importlib.util.module_from_spec(spec)
                sys.modules[module_name] = module
                spec.loader.exec_module(module)
                register_fn = getattr(module, "register", None)
                if not callable(register_fn):
                    logger.warning("适配器插件缺少 register() 入口: %s", path.name)
                    continue
                adapter = register_fn(adapter_registry, config, driver)
                if adapter is not None:
                    loaded.append((path.stem, adapter))
            except Exception:  # noqa: BLE001
                logger.exception("适配器插件加载失败: %s", path.name)
    return loaded


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
        meta = registry.plugin_metadata()
        if meta:
            lines = [f"📦 外部插件: {len(meta)} 个"]
            for stem, m in sorted(meta.items()):
                name = m.get("name") or stem
                ver = m.get("version", "")
                desc = m.get("description", "")
                line = f"· {name}" + (f" v{ver}" if ver else "")
                if desc:
                    line += f" — {desc}"
                lines.append(line)
        else:
            lines = ["📦 无外部插件"]
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
    """构建洋葱模型管道。

    顺序:Notice(通知) → RateLimit → ContentSafety → Security(访问控制)
    → Plugin(NoneBot 风格插件分发) → WakeCheck(唤醒) → PreProcess →
    Process(Agent) → Decorate → Respond。
    插件阶段在唤醒检测之前,因此 message/regex/command handler 能看到全部消息。
    """
    return PipelineScheduler(
        [
            NoticeStage(config, db=db, adapter_getter=adapter_getter),
            RateLimitStage(config),
            ContentSafetyStage(config),
            SecurityStage(config, auth),
            PluginStage(plugin_registry, config),
            WakeCheckStage(config),
            PreProcessStage(),
            ProcessStage(engine),
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

    # 反向驱动:共享 HTTP/WS 服务端(NoneBot Driver 风格)
    driver_cfg = config.get("driver", {})
    driver: ReverseDriver | None = None
    if bool(driver_cfg.get("enabled", True)):
        driver = ReverseDriver(
            host=driver_cfg.get("host", "127.0.0.1"),
            port=int(driver_cfg.get("port", 6199)),
        )

    # 管道
    pipeline = build_pipeline(
        config, auth, plugin_registry, engine,
        db=db,
        adapter_getter=lambda: adapter_registry.get("qq"),
    )
    adapter_registry.set_callback(pipeline.execute)

    # 插件依赖:适配器注册中心(供插件调用平台 API,如禁言/踢人)
    plugin_registry.register_dependency(AdapterRegistry, adapter_registry)

    # 非侵入式适配器加载:data/adapters/*.py 插件(新增平台无需修改 main.py)
    load_adapters(adapter_registry, config, driver)
    main_adapter = adapter_registry.get("qq")
    if main_adapter is None:
        # 兜底:无适配器插件加载时使用内置 OneBot v11 WS
        from .adapter import OneBotV11Adapter

        main_adapter = OneBotV11Adapter(
            host=config.get("onebot.host", "127.0.0.1"),
            port=int(config.get("onebot.port", 6199)),
            path=config.get("onebot.path", "/ws"),
            token=config.get("onebot.token", ""),
            self_id=config.get("onebot.self_id", ""),
        )
        adapter_registry.register("qq", main_adapter)

    # 回填 adapter:主 QQ 适配器作为默认发送通道
    engine.adapter = main_adapter
    cron._adapter = main_adapter

    # 启动服务
    await cron.start()
    if driver is not None:
        await driver.start()
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
    if driver is not None:
        await driver.stop()
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
