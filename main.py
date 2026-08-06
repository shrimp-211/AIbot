"""QQ AI Agent 入口:分步初始化 + 生命周期管理。

启动流程:基础设施(日志/存储/权限/记忆) → 工具注册 → Provider →
Agent 引擎 + 插件 → 管道 + OneBot 适配器 → Cron/WebUI → 信号监听。
"""
from __future__ import annotations

import argparse
import asyncio
import signal
import sys
import time
from pathlib import Path
from typing import Callable

from loguru import logger

from .adapter import AdapterRegistry, AgentEvent, BaseAdapter, ReverseDriver
from .adapter.message import escape_cq
from .agent.engine import AgentEngine
from .agent.hooks import HookManager
from .agent.mcp import MCPManager
from .agent.memory.files import FileMemoryStore
from .agent.memory.sqlite_store import SQLiteStore
from .agent.memory.store import MemoryStore
from .agent.perception import PerceptionManager
from .agent.persona import PersonaManager
from .agent.proactive import CronManager
from .agent.skills import SkillRegistry
from .agent.usage import UsageTracker
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
from .providers.base import (
    create_embedding_provider,
    create_provider,
    create_rerank_provider,
    create_stt_provider,
    create_tts_provider,
)
from .providers.modalities import MODALITY_IMAGE
from .security.audit import AuditLogger
from .security.auth import AuthManager
from .storage.db import JsonKV
from .utils.config import Config, load_config, load_dotenv
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

    import hashlib

    loaded: list[tuple[str, BaseAdapter]] = []
    for directory in _adapter_dirs():
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.py")):
            if path.name.startswith("_"):
                continue
            # 以路径哈希保证模块名唯一,避免不同目录同名文件互相覆盖
            digest = hashlib.sha1(str(path.resolve()).encode()).hexdigest()[:8]
            module_name = f"qqbot_adapter_{path.stem}_{digest}"
            sys.modules.pop(module_name, None)  # 清理缓存以支持热加载
            try:
                spec = importlib.util.spec_from_file_location(module_name, path)
                if spec is None or spec.loader is None:
                    logger.warning("适配器插件无法解析: {}", path.name)
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
                logger.exception("适配器插件加载失败: {}", path.name)
    return loaded


def register_builtin_plugins(
    registry: PluginRegistry,
    config: Config,
    auth: AuthManager,
    engine: AgentEngine,
) -> None:
    """注册内置插件:演示命令 + 会话控制命令体系(clear/cost/model/persona/skills/plan/session)。

    engine 提供模型切换/用量统计/会话清理等运行时能力(参照 AstrBot 命令体系)。
    """

    @registry.command("ping")
    async def ping(event: AgentEvent):
        await event.reply("pong 🏓")
        return None

    @registry.command("echo")
    async def echo(event: AgentEvent):
        arg = event.state.get("command_arg", "")
        if arg:
            await event.reply(escape_cq(arg))
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

    # ---------- 会话控制命令体系(参照 AstrBot) ----------

    @registry.command("clear", permission_level=1)
    async def clear(event: AgentEvent):
        engine.clear_session(event.session_id)
        await event.reply("✅ 已清空当前会话上下文。")
        return None

    @registry.command("cost", permission_level=1)
    async def cost(event: AgentEvent):
        # 私聊看自己,群聊看全局(避免暴露他人用量)
        user_id = event.user_id if event.message_type == "private" else ""
        s = engine.usage.summary(user_id=user_id)
        scope = "你的" if user_id else "全局"
        lines = [
            f"📊 LLM 用量统计({scope})",
            f"调用次数: {s['calls']}",
            f"输入 token: {s['prompt_tokens']:,}",
            f"输出 token: {s['completion_tokens']:,}",
            f"总 token: {s['total_tokens']:,}",
            f"估算成本: ${s['estimated_cost']:.4f}",
        ]
        await event.reply("\n".join(lines))
        return None

    @registry.command("model", permission_level=7)
    async def model(event: AgentEvent):
        arg = (event.state.get("command_arg") or "").strip()
        if not arg:
            await event.reply(f"当前模型: {escape_cq(engine.model_name)}")
            return None
        r = engine.set_model(arg)
        if r.get("error"):
            await event.reply(f"❌ {escape_cq(r['error'])}")
        else:
            await event.reply(f"✅ 已切换到模型: {escape_cq(r['model'])}")
        return None

    @registry.command("persona", permission_level=1)
    async def persona(event: AgentEvent):
        arg = (event.state.get("command_arg") or "").strip()
        if not arg:
            personas = engine.persona_manager.list()
            if not personas:
                await event.reply("暂无自定义人格,可用 /persona <id> 切换。")
                return None
            lines = ["📋 可用人格:"]
            for p in personas:
                lines.append(
                    f"· `{escape_cq(p['id'])}` — {escape_cq(p['name'])}: "
                    f"{escape_cq(p.get('description') or '无描述')}"
                )
            await event.reply("\n".join(lines))
            return None
        if arg in ("reset", "默认", "default"):
            r = engine.persona_manager.switch(event.session_id, None)
        else:
            r = engine.persona_manager.switch(event.session_id, arg)
        if r.get("error"):
            await event.reply(f"❌ {escape_cq(r['error'])}")
        else:
            await event.reply(f"✅ {escape_cq(r.get('message', '已切换人格'))}")
        return None

    @registry.command("skills", permission_level=1)
    async def skills(event: AgentEvent):
        arg = (event.state.get("command_arg") or "").strip()
        if not arg:
            all_skills = engine.skills.all()
            lines = ["🎯 可用技能:"] if all_skills else ["🎯 暂无技能。"]
            for s in all_skills:
                lines.append(f"· `{escape_cq(s.name)}` — {escape_cq(s.description or '无描述')}")
            active = engine.skills.active(event.session_id)
            lines.append(f"\n当前激活: {escape_cq(active.name) if active else '无'}")
            await event.reply("\n".join(lines))
            return None
        if arg in ("stop", "reset", "deactivate"):
            r = engine.skills.deactivate(event.session_id)
            await event.reply(f"✅ 已停止技能: {escape_cq(r.get('skill') or r.get('message') or '')}")
            return None
        r = engine.skills.activate(event.session_id, arg)
        if r.get("error"):
            await event.reply(f"❌ {escape_cq(r['error'])}")
        else:
            await event.reply(f"✅ 已激活技能 `{escape_cq(r.get('skill', ''))}` — {escape_cq(r.get('description') or '')}")
        return None

    @registry.command("plan", permission_level=1)
    async def plan(event: AgentEvent):
        plans = engine.db.get("plans", {}) or {}
        mine = [p for p in plans.values() if p.get("session") == event.session_id]
        if not mine:
            await event.reply("该会话暂无计划,可让 Agent 先用 plan 工具创建。")
            return None
        lines = ["📋 当前会话计划:"]
        for p in mine[-3:]:
            steps = p.get("steps", [])
            statuses = p.get("statuses", [])
            done = sum(1 for st in statuses if st == "completed")
            lines.append(f"· `{escape_cq(p['plan_id'])}` {escape_cq(p.get('goal', ''))} ({done}/{len(steps)})")
            for i, (step, st) in enumerate(zip(steps, statuses)):
                mark = {"completed": "✅", "in_progress": "🔄", "pending": "⬜"}.get(st, "⬜")
                lines.append(f"  {mark} {i + 1}. {escape_cq(step)}")
        await event.reply("\n".join(lines))
        return None

    @registry.command("session", permission_level=1)
    async def session(event: AgentEvent):
        parts = ((event.state.get("command_arg") or "").strip().split() or ["list"])
        action = parts[0].lower()
        if action == "save":
            r = engine.memory.save_checkpoint(event.session_id)
            await event.reply(f"✅ 已保存检查点: {r.get('messages', 0)} 条消息。")
        elif action == "load":
            r = engine.memory.load_checkpoint(event.session_id)
            if r is None:
                await event.reply("该会话没有已保存的检查点,可用 /session save 保存。")
            else:
                await event.reply(f"✅ 已恢复检查点({r.get('messages', 0)} 条消息)。")
        elif action in ("clear", "delete"):
            r = engine.memory.clear_checkpoint(event.session_id)
            await event.reply("✅ 已清除该会话检查点。" if r.get("deleted") else "该会话没有检查点。")
        else:  # list
            cps = engine.memory.list_checkpoints()
            if not cps:
                await event.reply("暂无已保存的检查点。")
                return None
            lines = ["💾 已保存的会话检查点:"]
            for cp in cps:
                ts = time.strftime("%m-%d %H:%M", time.localtime(cp.get("saved_at", 0)))
                lines.append(f"· `{escape_cq(cp['session_id'])}` — {ts} ({cp.get('messages', 0)} 条)")
            await event.reply("\n".join(lines))
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


def _warn_security_posture(config: Config) -> None:
    """启动时检查默认安全姿势,对高风险配置发出显著警告。

    - trusted_folders 空且 sandbox 关闭:bash/文件工具可访问宿主任意路径
    - super_admin_users 仍是示例占位符:任何知晓该号的用户拥有最高权限
    """
    trusted = list(config.get("security.trusted_folders", []) or [])
    sandbox = bool(config.get("security.sandbox_enabled", False))
    if not trusted and not sandbox:
        logger.warning(
            "⚠️ 安全提示: security.trusted_folders 为空且 sandbox_enabled=false,"
            "bash/文件工具可访问宿主任意路径。对外部署前请配置可信目录或启用沙箱。"
        )
    admins = set(config.get("security.super_admin_users", []) or [])
    if admins and "123456789" in admins:
        logger.warning(
            "⚠️ 安全提示: security.super_admin_users 仍为示例占位符 123456789,"
            "请改为你自己的 QQ 号,否则任何知晓该号的用户都拥有最高权限。"
        )


async def run(config_path: str | Path = DEFAULT_CONFIG, log_level: str = "INFO") -> None:
    # 加载 .env(项目根与代码目录,真实环境变量优先),供 config.yaml 的 ${ENV_VAR} 引用
    load_dotenv(ROOT_DIR / ".env")
    load_dotenv(BASE_DIR / ".env")
    config = load_config(config_path)
    setup_logger(level=log_level, log_dir=ROOT_DIR / "data" / "logs")
    logger.info(f"加载配置: {config_path}")

    # 存储
    db = JsonKV(ROOT_DIR / "data" / "agent.json")
    auth_db = JsonKV(ROOT_DIR / "data" / "auth.json")
    await db.initialize()
    await auth_db.initialize()
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

    # 默认安全姿势检查:首次部署时提醒关键风险
    _warn_security_posture(config)

    # 审计日志
    audit_logger = AuditLogger(
        ROOT_DIR / "data" / "audit.jsonl",
        enabled=bool(config.get("security.audit_enabled", True)),
    )

    # LLM 用量与成本统计(价格表可由 cost.prices 覆盖)
    usage = UsageTracker(db, prices=config.get("cost.prices", {}))

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

    # 多模态感知层(参照 AstrBot):STT/TTS/Embedding/Rerank Provider + PerceptionManager
    def _maybe_provider(factory: Callable, cfg: dict) -> Any:
        if not cfg or cfg.get("type") in (None, "", "none", "disabled"):
            return None
        try:
            return factory(cfg)
        except Exception as exc:  # noqa: BLE001
            logger.warning("{} Provider 初始化失败: {}", factory.__name__, exc)
            return None

    stt_provider = _maybe_provider(create_stt_provider, config.get("provider_stt", {}))
    tts_provider = _maybe_provider(create_tts_provider, config.get("provider_tts", {}))
    embedding_provider = _maybe_provider(
        create_embedding_provider, config.get("provider_embedding", {})
    )
    rerank_provider = _maybe_provider(create_rerank_provider, config.get("provider_rerank", {}))
    for p, label in (
        (stt_provider, "STT"), (tts_provider, "TTS"),
        (embedding_provider, "Embedding"), (rerank_provider, "Rerank"),
    ):
        if p is not None:
            logger.info("{} Provider: {}", label, type(p).__name__)

    async def _vision_analyzer(image_path: str, question: str) -> str:
        """多模态视觉问答:图片 → base64 → 主 LLM Provider 分析(同步读文件走 to_thread)。"""
        import base64
        import mimetypes

        def _read() -> tuple[str, str]:
            mime = mimetypes.guess_type(image_path)[0] or "image/jpeg"
            with open(image_path, "rb") as f:
                return mime, base64.b64encode(f.read()).decode()

        mime, b64 = await asyncio.to_thread(_read)
        # 按活动 provider 的图片块协议构建(ProviderManager 经 __getattr__ 委托)
        if getattr(provider, "image_block_format", "openai") == "anthropic":
            blocks: list[dict] = [
                {"type": "image", "source": {"type": "base64", "media_type": mime, "data": b64}}
            ]
        else:
            blocks = [{"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}]
        if question:
            blocks.append({"type": "text", "text": question})
        result = await provider.chat(messages=[{"role": "user", "content": blocks}])
        return (result.get("content") or "").strip()

    perception = PerceptionManager(
        stt_provider=stt_provider,
        # 主模型声明图像能力才注入视觉分析回调,否则感知器返回明确的能力缺失提示
        llm_analyzer=_vision_analyzer if MODALITY_IMAGE in provider.modalities else None,
    )

    # 向量知识库 + AIGC 生成层(参照 mainidea 知识层/生成层)
    from .agent.generation import GenerationManager
    from .agent.knowledge.manager import KnowledgeManager

    knowledge = KnowledgeManager(
        embedding_provider=embedding_provider,
        rerank_provider=rerank_provider,
        data_dir=ROOT_DIR / "data" / "knowledge",
    )
    generation = GenerationManager(
        config.get("generation", {}),
        tts_provider=tts_provider,
        output_dir=ROOT_DIR / "data" / "generated",
    )
    logger.info("知识库: {}", knowledge.stats())
    logger.info("生成层: {}", generation.stats())

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
        usage_tracker=usage,
        perception=perception,
        knowledge=knowledge,
        generation=generation,
    )

    # 插件注册中心
    plugin_registry = PluginRegistry(auth)
    plugin_registry.register_dependency(Config, config)
    plugin_registry.register_dependency(AuthManager, auth)
    plugin_registry.register_dependency(MemoryStore, memory)
    plugin_registry.register_dependency(JsonKV, db)  # 插件持久化数据
    register_builtin_plugins(plugin_registry, config, auth, engine)

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
            driver=driver,
        )
        adapter_registry.register("qq", main_adapter)

    # 回填 adapter:主 QQ 适配器作为默认发送通道(经公开 setter,避免构造后改私有属性)
    engine.set_adapter(main_adapter)
    cron.set_adapter(main_adapter)
    cron.set_engine(engine)  # 定时任务经主 Agent 生成内容(Cron active_agent)

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
            deps={
                "engine": engine, "tools": tools, "cron": cron,
                "plugin_registry": plugin_registry,
                "adapter_registry": adapter_registry,
            },
            config_path=config_path,
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
