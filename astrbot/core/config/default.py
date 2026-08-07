"""本项目定制的配置元数据与默认值(AstrBot 兼容)。

- ``DEFAULT_CONFIG`` 从本项目的 ``config.yaml`` 加载
- ``CONFIG_METADATA_2/3`` 从本项目的配置 schema 生成,供 dashboard 前端配置页渲染
"""

from __future__ import annotations

import os

import yaml

from astrbot.core.utils.astrbot_path import get_astrbot_path

VERSION = "1.0.0"
WEBHOOK_SUPPORTED_PLATFORMS = ["qq_official", "telegram"]
# 本项目 SQLModel 数据库(可选启用)
DB_PATH = os.path.join(os.path.dirname(get_astrbot_path()), "data", "app.sqlite3")

DEFAULT_VALUE_MAP = {
    "int": 0,
    "float": 0.0,
    "bool": False,
    "string": "",
    "text": "",
    "list": [],
    "file": [],
    "object": {},
    "template_list": [],
    "dict": {},
}


# ---------------- 本项目配置 schema(与 src/utils/config.py 的 CONFIG_SCHEMA 同构) ----------------

def _bool() -> dict:
    return {"type": bool}


def _int(lo=None, hi=None) -> dict:
    s = {"type": int}
    if lo is not None:
        s["min"] = lo
    if hi is not None:
        s["max"] = hi
    return s


def _str() -> dict:
    return {"type": str}


def _list() -> dict:
    return {"type": list}


_CONFIG_SCHEMA: dict = {
    "driver": {"type": dict, "children": {"enabled": _bool(), "host": _str(), "port": _int(1, 65535)}},
    "onebot": {"type": dict, "children": {
        "host": _str(), "port": _int(1, 65535), "path": _str(), "token": _str(), "self_id": _str()}},
    "onebot_forward": {"type": dict, "children": {
        "enabled": _bool(), "url": _str(), "token": _str(), "self_id": _str()}},
    "onebot_http": {"type": dict, "children": {
        "enabled": _bool(), "host": _str(), "port": _int(1, 65535), "path": _str(),
        "http_url": _str(), "token": _str(), "self_id": _str()}},
    "qq_official": {"type": dict, "children": {
        "enabled": _bool(), "host": _str(), "port": _int(1, 65535), "path": _str(),
        "app_id": _str(), "app_secret": _str(), "sign_secret": _str()}},
    "telegram": {"type": dict, "children": {
        "enabled": _bool(), "token": _str(), "allowed_chat_ids": _list(), "poll_timeout": _int(1, 50)}},
    "llm": {"type": dict, "children": {"provider": {"type": dict, "children": {
        "type": _str(), "model": _str(), "api_key": _str(), "base_url": _str(),
        "max_tokens": _int(1, 200000), "temperature": {"type": (int, float), "min": 0, "max": 2},
        "fallback_providers": _list()}}}},
    "search": {"type": dict, "children": {"tavily_key": _str(), "brave_key": _str()}},
    "mcp": {"type": dict, "children": {"servers": _list()}},
    "agent": {"type": dict, "children": {
        "workdir": _str(), "max_iterations": _int(1, 64), "max_context_tokens": _int(0, 1000000)}},
    "cron": {"type": dict, "children": {"agent_enabled": _bool()}},
    "notice": {"type": dict, "children": {
        "welcome": {"type": dict, "children": {"enabled": _bool(), "text": _str()}},
        "farewell": {"type": dict, "children": {"enabled": _bool(), "text": _str()}},
        "anti_recall": {"type": dict, "children": {"enabled": _bool(), "format": _str()}},
        "friend_requests": _str(), "group_requests": _str()}},
    "pipeline": {"type": dict, "children": {
        "group_whitelist": _list(), "wake_words": _list(), "command_prefixes": _list(),
        "rate_limit": {"type": dict, "children": {"max_messages": _int(1), "window_seconds": _int(1)}},
        "content_safety": {"type": dict, "children": {"max_length": _int(1)}}}},
    "security": {"type": dict, "children": {
        "admin_users": _list(), "super_admin_users": _list(), "trusted_folders": _list(),
        "sandbox_enabled": _bool(), "audit_enabled": _bool(), "pairing_enabled": _bool()}},
    "webui": {"type": dict, "children": {
        "enabled": _bool(), "host": _str(), "port": _int(1, 65535), "password": _str()}},
    "knowledge": {"type": dict, "children": {
        "embedding_model": _str(), "embedding_provider": _str(), "chunk_size": _int(1, 100000),
        "top_k": _int(1, 100), "rerank_enabled": _bool(), "fallback_enabled": _bool()}},
    "generation": {"type": dict, "children": {
        "image_provider": _str(), "image_api_key": _str(), "video_provider": _str(), "video_api_key": _str()}},
    "provider_stt": {"type": dict, "children": {"type": _str(), "model": _str(), "api_key": _str(), "base_url": _str()}},
    "provider_tts": {"type": dict, "children": {"type": _str(), "model": _str(), "api_key": _str(), "base_url": _str()}},
    "provider_embedding": {"type": dict, "children": {"type": _str(), "model": _str(), "api_key": _str(), "base_url": _str()}},
    "provider_rerank": {"type": dict, "children": {"type": _str(), "model": _str(), "api_key": _str(), "base_url": _str()}},
    "sandbox": {"type": dict, "children": {"mode": _str(), "max_sessions": _int(1, 64), "timeout": _int(1)}},
    "storage": {"type": dict, "children": {"sqlmodel": _bool()}},
}


def _schema_to_metadata(schema: dict) -> dict:
    meta: dict = {}
    for k, rule in schema.items():
        t = rule.get("type")
        desc = rule.get("description", "")
        hint = rule.get("hint", "")
        entry: dict = {"description": desc, "hint": hint}
        if t is dict:
            entry["type"] = "object"
            entry["items"] = _schema_to_metadata(rule.get("children", {}))
        elif t is list:
            entry["type"] = "list"
        elif t is bool:
            entry["type"] = "bool"
        elif t is int:
            entry["type"] = "int"
        elif t in (float, (int, float)):
            entry["type"] = "float"
        else:
            entry["type"] = "string"
        meta[k] = entry
    return meta


_SECTION_META = _schema_to_metadata(_CONFIG_SCHEMA)

# 兼容 AstrBot 的分组结构(platform/provider 组对本项目无模板,置空)
CONFIG_METADATA_2: dict = {
    "misc_config_group": {"metadata": _SECTION_META},
    "platform_group": {"metadata": {}},
    "provider_group": {"metadata": {}},
}

CONFIG_METADATA_3: dict = CONFIG_METADATA_2
CONFIG_METADATA_3_SYSTEM: dict = CONFIG_METADATA_2


def _load_default_config() -> dict:
    try:
        p = os.path.join(get_astrbot_path(), "config.yaml")
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
    except Exception:
        pass
    return {}


DEFAULT_CONFIG: dict = _load_default_config()
