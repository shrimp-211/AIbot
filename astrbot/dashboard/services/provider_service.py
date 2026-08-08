"""ProviderConfigService(本项目适配):映射本项目 config.yaml 的 5 类 provider 配置。

- llm.provider(对话)、provider_stt、provider_tts、provider_embedding、provider_rerank
- AstrBot 的 provider_sources(源)映射为"每类一个源",providers 映射为各类配置本身
"""

from __future__ import annotations

from typing import Any

from astrbot.core import logger
from astrbot.core.core_lifecycle import AstrBotCoreLifecycle

# 本项目 provider 段:key -> {capability, config_key, display}
PROVIDER_SECTIONS: dict[str, dict] = {
    "llm": {"capability": "chat_completion", "config_key": "llm.provider", "display": "LLM(对话模型)"},
    "stt": {"capability": "speech_to_text", "config_key": "provider_stt", "display": "语音识别(STT)"},
    "tts": {"capability": "text_to_speech", "config_key": "provider_tts", "display": "语音合成(TTS)"},
    "embedding": {"capability": "embedding", "config_key": "provider_embedding", "display": "向量嵌入(Embedding)"},
    "rerank": {"capability": "rerank", "config_key": "provider_rerank", "display": "重排序(Rerank)"},
}


class ProviderConfigService:
    def __init__(self, core_lifecycle: AstrBotCoreLifecycle) -> None:
        self.core_lifecycle = core_lifecycle
        self.config = core_lifecycle.astrbot_config

    # ---------------- 配置读写 ----------------

    def _get_cfg(self, dotted: str) -> dict:
        cur: Any = self.config
        for part in dotted.split("."):
            if not isinstance(cur, dict):
                return {}
            cur = cur.get(part, {})
        return cur if isinstance(cur, dict) else {}

    def _set_cfg(self, dotted: str, value: dict) -> None:
        parts = dotted.split(".")
        cur: dict = self.config
        for part in parts[:-1]:
            if not isinstance(cur.get(part), dict):
                cur[part] = {}
            cur = cur[part]
        cur[parts[-1]] = value
        try:
            self.config.save_config()
        except Exception as exc:  # noqa: BLE001
            logger.error("保存 provider 配置失败: %s", exc)

    def _to_provider(self, pid: str) -> dict | None:
        meta = PROVIDER_SECTIONS.get(pid)
        if meta is None:
            return None
        cfg = self._get_cfg(meta["config_key"])
        return {
            "id": pid,
            "provider_id": pid,
            "provider_source_id": pid,
            "type": cfg.get("type", pid),
            "provider": cfg.get("type", pid),
            "model": cfg.get("model"),
            "enable": bool(cfg.get("enabled", True)),
            "enabled": bool(cfg.get("enabled", True)),
            "capability": meta["capability"],
            "provider_type": meta["capability"],
            "config": cfg,
        }

    def _to_source(self, pid: str) -> dict:
        """返回 AstrBot 风格的 provider source(前端工作台渲染依赖这些字段)。"""
        meta = PROVIDER_SECTIONS[pid]
        cfg = self._get_cfg(meta["config_key"])
        return {
            "id": pid,
            "source_id": pid,
            "name": meta["display"],
            "type": pid,
            "provider_type": meta["capability"],
            "capability": meta["capability"],
            "provider": cfg.get("type") or pid,
            "key": cfg.get("api_key") or cfg.get("key") or "",
            "api_base": cfg.get("base_url") or cfg.get("api_base") or "",
            "model": cfg.get("model"),
            "enable": bool(cfg.get("enabled", True)),
            "enabled": bool(cfg.get("enabled", True)),
            "config": cfg,
            "created_at": None,
        }

    # ---------------- AstrBot 风格 schema / 模板 ----------------

    _SOURCE_ITEMS: dict = {
        "id": {"type": "string", "description": "配置 ID", "hint": "用于在系统中标识该提供商配置"},
        "type": {"type": "string", "description": "提供商类型", "hint": "如 llm / stt / tts / embedding / rerank"},
        "provider": {"type": "string", "description": "提供商名称"},
        "key": {"type": "string", "description": "API Key", "hint": "在对应平台申请的 API Key"},
        "api_base": {"type": "string", "description": "API 地址", "hint": "留空使用默认地址"},
        "proxy": {"type": "string", "description": "代理地址", "hint": "可选,格式 http://host:port"},
        "model": {"type": "string", "description": "默认模型"},
        "enable": {"type": "bool", "description": "是否启用"},
    }

    def _build_config_schema(self) -> dict:
        """构建前端 Provider 工作台需要的 config_schema(provider.items + config_template)。"""
        templates = {}
        for pid, meta in PROVIDER_SECTIONS.items():
            templates[pid] = {
                "id": pid,
                "type": pid,
                "provider_type": meta["capability"],
                "provider": pid,
                "enable": True,
                "model": "",
                "key": "",
                "api_base": "",
            }
        return {
            "provider": {
                "type": "object",
                "items": dict(self._SOURCE_ITEMS),
                "config_template": templates,
            }
        }

    @staticmethod
    def _resolve_section(source_id: str, config: dict) -> str | None:
        """把 source_id / config 解析到本项目 PROVIDER_SECTIONS 的 key。"""
        if source_id in PROVIDER_SECTIONS:
            return source_id
        for key, meta in PROVIDER_SECTIONS.items():
            if meta["capability"] in (config.get("capability"), config.get("provider_type")):
                return key
            if config.get("type") == key:
                return key
        return None

    @classmethod
    def _resolve_provider_pid(
        cls,
        provider_id: str | None,
        config: dict | None = None,
    ) -> str | None:
        """把 provider_id / provider 配置解析到 PROVIDER_SECTIONS 的 key。

        provider 实例 id 形如 ``llm/gpt-4o``,需回退到 section key ``llm``。
        """
        candidates: list[str] = []
        if provider_id:
            candidates.append(str(provider_id))
        if isinstance(config, dict):
            for key in ("provider_source_id", "source_id", "provider_id", "id"):
                val = config.get(key)
                if val:
                    candidates.append(str(val))
        for c in candidates:
            if c in PROVIDER_SECTIONS:
                return c
        if provider_id and "/" in provider_id:
            prefix = str(provider_id).split("/", 1)[0]
            if prefix in PROVIDER_SECTIONS:
                return prefix
        return None

    @staticmethod
    def _to_project_config(config: dict, pid: str) -> dict:
        """把 AstrBot 风格 source 配置映射回本项目 provider 段字段。"""
        current = config  # 调用方已合并
        merged = {
            "type": current.get("type") or pid,
            "model": current.get("model", ""),
            "api_key": current.get("key") or current.get("api_key") or "",
            "base_url": current.get("api_base") or current.get("base_url") or "",
            "enabled": bool(current.get("enable", current.get("enabled", True))),
        }
        for key in ("max_tokens", "temperature", "fallback_providers"):
            if key in current:
                merged[key] = current[key]
        return merged

    # ---------------- schema / 源 ----------------

    def get_provider_schema(self) -> dict:
        return {
            "config_schema": self._build_config_schema(),
            "providers": self.list_providers()["providers"],
            "provider_sources": self.list_provider_sources()["provider_sources"],
            "model_metadata": {},
        }

    def list_provider_sources(self) -> dict:
        return {
            "provider_sources": [self._to_source(pid) for pid in PROVIDER_SECTIONS],
        }

    def get_provider_source(self, source_id: str) -> dict | None:
        if source_id not in PROVIDER_SECTIONS:
            return None
        return self._to_source(source_id)

    async def upsert_provider_source(self, source_id: str, config: dict) -> dict:
        if not isinstance(config, dict):
            raise ValueError("配置格式错误")
        pid = self._resolve_section(source_id, config)
        if pid is None:
            raise ValueError(f"未知 provider source: {source_id}")
        meta = PROVIDER_SECTIONS[pid]
        current = self._get_cfg(meta["config_key"])
        merged = {**current, **config}
        if "enable" in config and "enabled" not in merged:
            merged["enabled"] = bool(config["enable"])
        self._set_cfg(meta["config_key"], self._to_project_config(merged, pid))
        return self._to_source(pid)

    async def delete_provider_source(self, source_id: str) -> None:
        meta = PROVIDER_SECTIONS.get(source_id)
        if meta is not None:
            self._set_cfg(meta["config_key"], {})

    # ---------------- provider 实例 ----------------

    CAPABILITY_ALIAS = {
        "chat": "chat_completion",
        "agent": "agent_runner",
        "stt": "speech_to_text",
        "tts": "text_to_speech",
    }

    @classmethod
    def _normalize_capability(cls, capability: str | None) -> str | None:
        if not capability:
            return None
        return cls.CAPABILITY_ALIAS.get(capability, capability)

    def list_providers(
        self,
        *,
        capability: str | None = None,
        source_id: str | None = None,
        enabled: bool | None = None,
    ) -> dict:
        capability = self._normalize_capability(capability)
        providers = []
        for pid, meta in PROVIDER_SECTIONS.items():
            if capability and meta["capability"] != capability:
                continue
            if source_id and pid != source_id:
                continue
            if self._get_cfg(meta["config_key"]):
                p = self._to_provider(pid)
                if enabled is not None and p["enable"] != enabled:
                    continue
                providers.append(p)
        return {"providers": providers}

    def get_provider(self, provider_id: str, merged: bool = True) -> dict | None:
        pid = self._resolve_provider_pid(provider_id)
        if pid is None:
            return None
        return self._to_provider(pid)

    async def create_provider(self, config: dict, source_id: str | None = None) -> dict:
        if source_id and not config.get("provider_source_id"):
            config = {**config, "provider_source_id": source_id}
        pid = self._resolve_provider_pid(None, config)
        if pid is None:
            raise ValueError(f"未知 provider: {config.get('id') or config.get('provider_source_id')}")
        meta = PROVIDER_SECTIONS.get(pid)
        pcfg = config.get("config") if isinstance(config, dict) else None
        if pcfg is None:
            pcfg = config
        if not isinstance(pcfg, dict):
            raise ValueError("配置格式错误")
        merged = {**self._get_cfg(meta["config_key"]), **pcfg}
        if "enable" in config and "enabled" not in merged:
            merged["enabled"] = bool(config["enable"])
        self._set_cfg(meta["config_key"], self._to_project_config(merged, pid))
        return self._to_provider(pid) or {}

    async def update_provider(self, provider_id: str, config: dict) -> dict:
        pid = self._resolve_provider_pid(provider_id, config)
        if pid is None:
            raise ValueError(f"未知 provider: {provider_id}")
        meta = PROVIDER_SECTIONS.get(pid)
        pcfg = config.get("config") if isinstance(config, dict) else None
        if pcfg is None:
            pcfg = config
        if not isinstance(pcfg, dict):
            raise ValueError("配置格式错误")
        merged = {**self._get_cfg(meta["config_key"]), **pcfg}
        self._set_cfg(meta["config_key"], self._to_project_config(merged, pid))
        return self._to_provider(pid) or {}

    async def delete_provider(self, provider_id: str) -> None:
        pid = self._resolve_provider_pid(provider_id)
        if pid is not None:
            self._set_cfg(PROVIDER_SECTIONS[pid]["config_key"], {})

    async def set_provider_enabled(self, provider_id: str, enabled: bool) -> dict:
        pid = self._resolve_provider_pid(provider_id)
        if pid is None:
            raise ValueError(f"未知 provider: {provider_id}")
        meta = PROVIDER_SECTIONS.get(pid)
        cfg = {**self._get_cfg(meta["config_key"]), "enabled": bool(enabled)}
        self._set_cfg(meta["config_key"], cfg)
        return self._to_provider(pid) or {}

    @staticmethod
    def _test_result(pid: str, cfg: dict, available: bool, message: str) -> dict:
        return {
            "id": pid,
            "model": cfg.get("model"),
            "type": cfg.get("type"),
            "name": pid,
            "status": "available" if available else "unavailable",
            "error": None if available else message,
        }

    async def test_provider(self, provider_id: str) -> dict:
        """测试 provider 连通性(返回 AstrBot 前端期望的 status/error 结构)。"""
        pid = self._resolve_provider_pid(provider_id)
        if pid is None:
            raise ValueError(f"未知 provider: {provider_id}")
        meta = PROVIDER_SECTIONS.get(pid)
        cfg = self._get_cfg(meta["config_key"])
        if not cfg.get("api_key") and pid != "tts":
            return self._test_result(pid, cfg, False, "未配置 api_key,请先在配置中填写")
        project_provider = None
        pm = self.core_lifecycle.provider_manager
        if pm is not None and hasattr(pm, "project_provider"):
            project_provider = pm.project_provider
        if pid == "llm" and project_provider is not None:
            try:
                if hasattr(project_provider, "test"):
                    ok, msg = await project_provider.test()
                    return self._test_result(
                        pid, cfg, bool(ok), "" if ok else str(msg or "测试失败")
                    )
            except Exception as exc:  # noqa: BLE001
                return self._test_result(pid, cfg, False, f"测试失败: {exc}")
        return self._test_result(pid, cfg, True, "配置已保存(重启生效)")

    async def get_embedding_dimension(self, source_id: str, config: dict | None = None) -> dict:
        return {"dimension": None}

    async def get_embedding_dimension_from_dashboard_payload(self, data: dict) -> dict:
        return await self.get_embedding_dimension(data.get("source_id"), data.get("config"))

    async def list_provider_source_models(self, source_id: str) -> list[dict]:
        meta = PROVIDER_SECTIONS.get(source_id)
        if meta is None:
            return []
        cfg = self._get_cfg(meta["config_key"])
        model = cfg.get("model")
        return [{"model_name": model, "id": model}] if model else []

    async def list_provider_models_for_dashboard(self, source_id: str, config: dict | None = None) -> list[dict]:
        return await self.list_provider_source_models(source_id)
