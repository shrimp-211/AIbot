"""AstrBot 管理器兼容适配:把 dashboard 依赖的 AstrBot 管理器接口映射到本项目服务。

- PersonaManagerCompat → src.agent.persona.PersonaManager
- CronManagerCompat → src.agent.proactive.CronManager
- PlatformManagerCompat → src.adapter.AdapterRegistry
- ProviderManagerCompat → src.providers(工厂 + config)
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from astrbot.core.sentinels import NOT_GIVEN
from astrbot.core.star.star_manager import PluginManager


# ---------------- 人格 ----------------

class CompatPersona:
    """AstrBot persona_service 期望的人格对象。"""

    def __init__(
        self,
        persona_id: str,
        system_prompt: str = "",
        begin_dialogs: list | None = None,
        tools=None,
        skills=None,
        custom_error_message: str | None = None,
        folder_id: str | None = None,
        sort_order: int = 0,
        name: str = "",
    ) -> None:
        self.persona_id = persona_id
        self.id = persona_id
        self.name = name or persona_id
        self.system_prompt = system_prompt
        self.begin_dialogs = begin_dialogs or []
        self.tools = tools
        self.skills = skills
        self.custom_error_message = custom_error_message
        self.folder_id = folder_id
        self.sort_order = sort_order
        now = datetime.now(timezone.utc)
        self.created_at = now
        self.updated_at = now


class PersonaManagerCompat:
    def __init__(self, project_persona) -> None:
        self._pm = project_persona

    def _to_compat(self, pid: str) -> CompatPersona | None:
        p = self._pm.get(pid) if self._pm else None
        if p is None:
            return None
        return CompatPersona(
            persona_id=pid,
            system_prompt=p.get("system_prompt", ""),
            begin_dialogs=p.get("begin_dialogs") or [],
            tools=p.get("tool_allowlist"),
            skills=p.get("skills"),
            custom_error_message=p.get("custom_error_message"),
            name=p.get("name", pid),
        )

    async def get_all_personas(self) -> list[CompatPersona]:
        if self._pm is None:
            return []
        out = []
        for it in self._pm.list():
            comp = self._to_compat(it["id"])
            if comp:
                out.append(comp)
        return out

    async def get_persona(self, persona_id: str) -> CompatPersona | None:
        return self._to_compat(persona_id)

    async def get_personas_by_folder(self, folder_id=None) -> list[CompatPersona]:
        return await self.get_all_personas()

    async def create_persona(
        self,
        persona_id: str,
        system_prompt: str,
        begin_dialogs=None,
        tools=None,
        skills=None,
        custom_error_message=None,
        folder_id=None,
        sort_order=0,
    ) -> CompatPersona:
        if self._pm is None:
            raise RuntimeError("人格管理器未配置")
        self._pm.create(
            persona_id=persona_id,
            name=persona_id,
            system_prompt=system_prompt,
            begin_dialogs=begin_dialogs,
            tool_allowlist=tools,
        )
        comp = self._to_compat(persona_id)
        if comp is None:
            raise RuntimeError(f"人格创建失败: {persona_id}")
        return comp

    async def update_persona(
        self,
        persona_id: str,
        system_prompt=None,
        begin_dialogs=None,
        tools: Any = NOT_GIVEN,
        skills: Any = NOT_GIVEN,
        custom_error_message: Any = NOT_GIVEN,
    ) -> CompatPersona | None:
        if self._pm is None:
            return None
        fields: dict = {}
        if system_prompt is not None:
            fields["system_prompt"] = system_prompt
        if begin_dialogs is not None:
            fields["begin_dialogs"] = begin_dialogs
        if tools is not NOT_GIVEN:
            fields["tool_allowlist"] = tools
        if custom_error_message is not NOT_GIVEN:
            fields["custom_error_message"] = custom_error_message
        if fields:
            self._pm.update(persona_id, **fields)
        return self._to_compat(persona_id)

    async def delete_persona(self, persona_id: str) -> None:
        if self._pm is not None:
            self._pm.delete(persona_id)

    # ---- 文件夹(本项目无文件夹概念,返回空) ----
    async def get_folders(self, parent_id=None) -> list:
        return []

    async def get_folder_tree(self, parent_id=None) -> list:
        return []

    async def create_folder(self, *args, **kwargs):
        return None

    async def get_folder(self, folder_id: str):
        return None

    async def update_folder(self, *args, **kwargs):
        return None

    async def delete_folder(self, folder_id: str) -> None:
        return None

    async def move_persona_to_folder(self, persona_id: str, folder_id: str | None):
        return await self.get_persona(persona_id)

    async def batch_update_sort_order(self, items: list[dict]) -> None:
        return None


# ---------------- 定时任务 ----------------

class CompatCronJob:
    def __init__(
        self,
        job_id: str,
        name: str = "",
        job_type: str = "cron",
        cron_expression: str | None = None,
        description: str | None = None,
        enabled: bool = True,
        payload: dict | None = None,
        next_run_time=None,
        last_run_at=None,
    ) -> None:
        self.job_id = job_id
        self.name = name or job_id
        self.job_type = job_type
        self.cron_expression = cron_expression
        self.description = description
        self.enabled = enabled
        self.payload = payload or {}
        self.next_run_time = next_run_time
        self.last_run_at = last_run_at


class CronManagerCompat:
    def __init__(self, project_cron) -> None:
        self._cron = project_cron
        self.db = None

    def _to_job(self, task: dict) -> CompatCronJob:
        return CompatCronJob(
            job_id=str(task.get("id", "")),
            name=task.get("text", "")[:50],
            job_type="cron",
            cron_expression=task.get("when"),
            description=task.get("text"),
            enabled=task.get("enabled", True),
            payload=task,
        )

    async def list_jobs(self, job_type: str | None = None) -> list[CompatCronJob]:
        if self._cron is None:
            return []
        try:
            tasks = self._cron.list_tasks()
            return [self._to_job(t) for t in tasks]
        except Exception:
            return []

    async def add_active_job(self, *args, **kwargs):
        return None

    async def update_job(self, *args, **kwargs):
        return None

    async def delete_job(self, job_id: str) -> None:
        if self._cron is not None:
            try:
                self._cron.delete_task(None, job_id)
            except Exception:
                pass

    async def run_job_now(self, job_id: str):
        return None


# ---------------- 平台 ----------------

class PlatformManagerCompat:
    def __init__(self, adapter_registry=None) -> None:
        self._adapter_registry = adapter_registry
        self.platform_insts: dict = {}

    def get_insts(self):
        return []

    async def get_all_stats(self):
        return []

    async def load_platform(self, *args, **kwargs):
        return None

    async def reload(self, *args, **kwargs):
        return None

    async def terminate_platform(self, *args, **kwargs):
        return None


# ---------------- Provider ----------------

class _ToolItem:
    """工具项(兼容 AstrBot func_list 元素,含 .name/.description 等属性)。"""

    def __init__(self, name: str, description: str = "") -> None:
        self.name = name
        self.description = description
        self.enabled = True
        self.handler_full_name = f"builtin::{name}"
        self.attributes: list = []


class CompatTools:
    """包装本项目 ToolRegistry + MCP 配置,提供 AstrBot 工具管理器接口。"""

    def __init__(self, tools=None, config=None) -> None:
        self._tools = tools
        self._config = config

    def names(self) -> list[str]:
        if self._tools is not None and hasattr(self._tools, "names"):
            try:
                return list(self._tools.names())
            except Exception:
                pass
        return []

    def schemas(self) -> list[dict]:
        if self._tools is not None and hasattr(self._tools, "schemas"):
            try:
                return list(self._tools.schemas())
            except Exception:
                pass
        return []

    @property
    def func_list(self) -> list:
        """AstrBot 风格工具列表(属性,元素带 .name,供 WebUI 工具页渲染)。"""
        items = []
        seen: set[str] = set()
        try:
            for schema in self.schemas():
                if isinstance(schema, dict) and schema.get("function", {}).get("name"):
                    fn = schema["function"]
                    n = fn.get("name")
                    if n and n not in seen:
                        seen.add(n)
                        items.append(_ToolItem(n, fn.get("description", "")))
        except Exception:
            pass
        for name in self.names():
            if name not in seen:
                items.append(_ToolItem(name))
        return items

    def iter_builtin_tools(self):
        return iter([])

    def is_builtin_tool(self, name: str) -> bool:
        return False

    def load_mcp_config(self) -> dict:
        """读取 MCP 服务器配置。"""
        try:
            servers = self._config.get("mcp.servers", []) if self._config else []
            if isinstance(servers, dict):
                return {"mcpServers": servers}
            if isinstance(servers, list):
                return {
                    "mcpServers": {
                        str(s.get("name", f"server_{i}")): s
                        for i, s in enumerate(servers)
                        if isinstance(s, dict)
                    }
                }
        except Exception:
            pass
        return {"mcpServers": {}}

    def save_mcp_config(self, config: dict) -> bool:
        try:
            servers = list((config or {}).get("mcpServers", {}).values())
            if self._config is not None:
                self._config["mcp"] = {"servers": servers}
                self._config.save_config()
            return True
        except Exception:
            return False


class ProviderManagerCompat:
    """Provider 管理器(映射本项目 providers 配置与实例)。"""

    def __init__(
        self,
        config=None,
        project_provider=None,
        tools=None,
        provider_sources=None,
    ) -> None:
        self.config = config
        self.provider = project_provider  # 本项目 LLM Provider
        self.llm_tools = CompatTools(tools, config)  # 本项目 ToolRegistry 包装
        self.provider_sources = provider_sources or {}
        self.providers_config: dict = {}
        self.provider_sources_config: dict = {}
        self.inst_map: dict = {}

    async def get_provider_config_by_id(self, provider_id: str):
        if self.config is None:
            return None
        return self.config.get("llm.provider", {})

    async def get_merged_provider_config(self, provider_id: str):
        return await self.get_provider_config_by_id(provider_id)

    async def create_provider(self, *args, **kwargs):
        return None

    async def update_provider(self, *args, **kwargs):
        return None

    async def delete_provider(self, *args, **kwargs):
        return None

    async def dynamic_import_provider(self, *args, **kwargs):
        return None
