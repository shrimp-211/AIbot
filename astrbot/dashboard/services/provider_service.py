"""ProviderConfigService(本项目适配):映射本项目 config.yaml 的 5 类 provider 配置。

- llm.provider(对话)、provider_stt、provider_tts、provider_embedding、provider_rerank
- AstrBot 的 provider_sources(源)映射为"每类一个源",providers 映射为各类配置本身
"""

from __future__ import annotations

import copy
from typing import Any

from astrbot.core import logger
from astrbot.core.config.default import CONFIG_METADATA_2
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
            "model": cfg.get("model"),
            "enable": bool(cfg.get("enabled", True)),
            "enabled": bool(cfg.get("enabled", True)),
            "capability": meta["capability"],
            "provider_type": meta["capability"],
            "config": cfg,
        }

    def _to_source(self, pid: str) -> dict:
        meta = PROVIDER_SECTIONS[pid]
        cfg = self._get_cfg(meta["config_key"])
        return {
            "source_id": pid,
            "name": meta["display"],
            "type": pid,
            "capability": meta["capability"],
            "config": cfg,
            "enable": True,
            "created_at": None,
        }

    # ---------------- schema / 源 ----------------

    def get_provider_schema(self) -> dict:
        return {
            "config_schema": copy.deepcopy(CONFIG_METADATA_2),
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
        meta = PROVIDER_SECTIONS.get(source_id)
        if meta is None:
            raise ValueError(f"未知 provider source: {source_id}")
        if not isinstance(config, dict):
            raise ValueError("配置格式错误")
        current = self._get_cfg(meta["config_key"])
        merged = {**current, **config}
        if "enable" in config and "enabled" not in merged:
            merged["enabled"] = bool(config["enable"])
        self._set_cfg(meta["config_key"], merged)
        return self._to_source(source_id)

    async def delete_provider_source(self, source_id: str) -> None:
        meta = PROVIDER_SECTIONS.get(source_id)
        if meta is not None:
            self._set_cfg(meta["config_key"], {})

    # ---------------- provider 实例 ----------------

    def list_providers(
        self,
        *,
        capability: str | None = None,
        source_id: str | None = None,
        enabled: bool | None = None,
    ) -> dict:
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
        return self._to_provider(provider_id)

    async def create_provider(self, config: dict) -> dict:
        pid = str(config.get("provider_id") or config.get("id") or config.get("provider_source_id") or config.get("source_id") or "")
        meta = PROVIDER_SECTIONS.get(pid)
        if meta is None:
            raise ValueError(f"未知 provider: {pid}")
        pcfg = config.get("config") if isinstance(config, dict) else None
        if pcfg is None:
            pcfg = config
        if not isinstance(pcfg, dict):
            raise ValueError("配置格式错误")
        merged = {**self._get_cfg(meta["config_key"]), **pcfg}
        if "enable" in config and "enabled" not in merged:
            merged["enabled"] = bool(config["enable"])
        self._set_cfg(meta["config_key"], merged)
        return self._to_provider(pid) or {}

    async def update_provider(self, provider_id: str, config: dict) -> dict:
        meta = PROVIDER_SECTIONS.get(provider_id)
        if meta is None:
            raise ValueError(f"未知 provider: {provider_id}")
        pcfg = config.get("config") if isinstance(config, dict) else None
        if pcfg is None:
            pcfg = config
        if not isinstance(pcfg, dict):
            raise ValueError("配置格式错误")
        merged = {**self._get_cfg(meta["config_key"]), **pcfg}
        self._set_cfg(meta["config_key"], merged)
        return self._to_provider(provider_id) or {}

    async def delete_provider(self, provider_id: str) -> None:
        meta = PROVIDER_SECTIONS.get(provider_id)
        if meta is not None:
            self._set_cfg(meta["config_key"], {})

    async def set_provider_enabled(self, provider_id: str, enabled: bool) -> dict:
        meta = PROVIDER_SECTIONS.get(provider_id)
        if meta is None:
            raise ValueError(f"未知 provider: {provider_id}")
        cfg = {**self._get_cfg(meta["config_key"]), "enabled": bool(enabled)}
        self._set_cfg(meta["config_key"], cfg)
        return self._to_provider(provider_id) or {}

    async def test_provider(self, provider_id: str) -> dict:
        """测试 provider 连通性(调用本项目 provider 的 test 方法)。"""
        meta = PROVIDER_SECTIONS.get(provider_id)
        if meta is None:
            raise ValueError(f"未知 provider: {provider_id}")
        cfg = self._get_cfg(meta["config_key"])
        if not cfg.get("api_key") and provider_id != "tts":
            return {"ok": False, "message": "未配置 api_key,请先在配置中填写"}
        project_provider = None
        pm = self.core_lifecycle.provider_manager
        if pm is not None and hasattr(pm, "project_provider"):
            project_provider = pm.project_provider
        if provider_id == "llm" and project_provider is not None:
            try:
                if hasattr(project_provider, "test"):
                    ok, msg = await project_provider.test()
                    return {"ok": bool(ok), "message": str(msg)}
            except Exception as exc:  # noqa: BLE001
                return {"ok": False, "message": f"测试失败: {exc}"}
        return {"ok": True, "message": "配置已保存(重启生效)"}

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
