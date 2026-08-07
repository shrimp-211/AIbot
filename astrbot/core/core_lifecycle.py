"""AstrBotCoreLifecycle 兼容门面:把 AstrBot dashboard 依赖的核心接口映射到本项目。

main.py 创建本实例并调用 :meth:`bind_project` 绑定本项目服务,随后把本实例交给
``astrbot.dashboard.api.app.create_dashboard_asgi_app`` 组装 ASGI 应用。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from astrbot.core import get_db_helper, logger
from astrbot.core.config.astrbot_config import AstrBotConfig
from astrbot.core.conversation_mgr import ConversationManager
from astrbot.core.core_managers import (
    CronManagerCompat,
    PersonaManagerCompat,
    PlatformManagerCompat,
    ProviderManagerCompat,
)
from astrbot.core.log import LogBroker
from astrbot.core.star.star_manager import PluginManager, StarContext
from astrbot.core.updater import AstrBotUpdater


class _CompatAstrBotConfigManager:
    """多配置 profile 兼容(本项目单配置)。"""

    def __init__(self, default_conf) -> None:
        self.default_conf = default_conf
        self.confs: dict = {"default": default_conf}

    def get_conf(self, conf_id: str | None = None):
        return self.confs.get(conf_id or "default", self.default_conf)

    def get_conf_list(self) -> list[dict]:
        return [{"id": "default", "name": "默认配置", "description": "本项目的 config.yaml"}]

    def create_conf(self, name: str | None = None, config: dict | None = None) -> str:
        return "default"

    def delete_conf(self, conf_id: str) -> None:
        return None

    def update_conf_info(self, conf_id: str, name: str | None = None) -> None:
        return None


class _UmopConfigRouterCompat:
    """UMO → 配置 profile 路由(本项目统一使用默认配置)。"""

    def __init__(self) -> None:
        self.routes: dict = {}

    def get_routes(self) -> dict:
        return self.routes

    async def update_route(self, umo: str, conf_id: str) -> None:
        self.routes[umo] = conf_id

    async def delete_route(self, umo: str) -> None:
        self.routes.pop(umo, None)


class _SubagentOrchestratorCompat:
    def __init__(self) -> None:
        self.config: dict = {}

    def get_subagent_config(self) -> dict:
        return self.config


class AstrBotCoreLifecycle:
    """dashboard 依赖的门面对象。"""

    def __init__(self) -> None:
        self.astrbot_config = AstrBotConfig.get_singleton() or AstrBotConfig()
        self.db = get_db_helper()
        self.log_broker = LogBroker()
        self.start_time: float = time.time()
        self.dashboard_shutdown_event = asyncio.Event()
        self.pipeline_scheduler_mapping: dict = {}
        self.event_queue: asyncio.Queue = asyncio.Queue()
        self.event_bus = None
        self.star_context = StarContext()
        self.astrbot_updater = AstrBotUpdater()
        self.umop_config_router = _UmopConfigRouterCompat()
        self.subagent_orchestrator = _SubagentOrchestratorCompat()
        self.astrbot_config_mgr = _CompatAstrBotConfigManager(self.astrbot_config)

        # 各 manager 默认初始化为 compat 实例;bind_project 会替换为本项目服务映射
        self.conversation_manager = ConversationManager(self.db)
        self.platform_message_history_manager = None
        self.plugin_manager = PluginManager()
        self.provider_manager = ProviderManagerCompat(config=self.astrbot_config)
        self.platform_manager = PlatformManagerCompat()
        self.persona_mgr = PersonaManagerCompat(None)
        self.cron_manager = CronManagerCompat(None)
        from astrbot.core.knowledge_base.kb_mgr import KnowledgeBaseManager

        self.kb_manager = KnowledgeBaseManager(None)

        self._engine = None  # 本项目 AgentEngine
        self._restart_requested = False
        # 本项目服务(供 dashboard services 映射)
        self.skills = None  # src.agent.skills.SkillRegistry
        self.generation = None  # src.agent.generation.GenerationManager
        self.stt_provider = None
        self.tts_provider = None

    # ---------------- 绑定本项目服务 ----------------

    def bind_project(
        self,
        *,
        config=None,
        config_path: str | None = None,
        engine=None,
        tools=None,
        provider=None,
        persona=None,
        cron=None,
        knowledge=None,
        plugin_registry=None,
        plugin_installer=None,
        plugin_dirs: list[str] | None = None,
        adapter_registry=None,
        skills=None,
        generation=None,
        stt_provider=None,
        tts_provider=None,
    ) -> None:
        """绑定本项目核心服务,构建各 compat 管理器。"""
        if config is not None:
            AstrBotConfig.bind_project_config(config, config_path)
            self.astrbot_config = AstrBotConfig.get_singleton()
            self.astrbot_config_mgr = _CompatAstrBotConfigManager(self.astrbot_config)
        self._engine = engine
        self.skills = skills
        self.generation = generation
        self.stt_provider = stt_provider
        self.tts_provider = tts_provider
        self.persona_mgr = PersonaManagerCompat(persona)
        self.cron_manager = CronManagerCompat(cron)
        self.platform_manager = PlatformManagerCompat(adapter_registry)
        self.provider_manager = ProviderManagerCompat(
            config=self.astrbot_config,
            project_provider=provider,
            tools=tools,
        )
        from astrbot.core.knowledge_base.kb_mgr import KnowledgeBaseManager

        self.kb_manager = KnowledgeBaseManager(knowledge)
        self.plugin_manager = PluginManager(
            plugin_registry=plugin_registry,
            installer=plugin_installer,
            plugin_dirs=plugin_dirs,
        )
        self.star_context = StarContext()

    # ---------------- 生命周期 ----------------

    async def initialize(self) -> None:
        await self.db.initialize()

    async def start(self) -> None:
        try:
            await self.db.initialize()
        except Exception as exc:  # noqa: BLE001
            logger.error(f"dashboard 数据库初始化失败: {exc}")

    async def stop(self) -> None:
        self.dashboard_shutdown_event.set()

    async def restart(self) -> None:
        self._restart_requested = True
        self.dashboard_shutdown_event.set()

    def request_restart(self) -> None:
        self._restart_requested = True
        self.dashboard_shutdown_event.set()

    async def reload_pipeline_scheduler(self, conf_id: str | None = None) -> None:
        """配置变更后重载(本项目为单配置,保存即生效,无需重载管道)。"""
        return None
