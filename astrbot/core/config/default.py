"""本项目定制的配置元数据与默认值(AstrBot 兼容)。

- ``DEFAULT_CONFIG`` 从本项目的 ``config.yaml`` 加载
- ``CONFIG_METADATA_2/3`` 从本项目的配置 schema 生成,供 dashboard 前端配置页渲染

结构说明:
- 顶层按 AstrBot Dashboard 前端期望的 group 划分(ai_group/platform_group/
  plugin_group/ext_group/system_group/provider_group),每个 group 带 ``name``(i18n key)。
- group 内 ``metadata`` 的每个条目是 ``{section: {type:"object", description,
  hint, items}}``;items 的键使用**点号路径**(如 ``llm.provider.type``),
  前端 AstrBotConfigV4 通过点号 selector 在 config_data 上取值/写值。
- platform/provider 条目额外带 ``config_template``,供平台/提供商新增弹窗使用。
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

def _bool(desc: str = "", hint: str = "") -> dict:
    return {"type": bool, "description": desc, "hint": hint}


def _int(lo=None, hi=None, desc: str = "", hint: str = "") -> dict:
    s = {"type": int, "description": desc, "hint": hint}
    if lo is not None:
        s["min"] = lo
    if hi is not None:
        s["max"] = hi
    return s


def _str(desc: str = "", hint: str = "") -> dict:
    return {"type": str, "description": desc, "hint": hint}


def _list(desc: str = "", hint: str = "") -> dict:
    return {"type": list, "description": desc, "hint": hint}


def _obj(desc: str = "", hint: str = "", **children) -> dict:
    return {"type": dict, "description": desc, "hint": hint, "children": children}


_CONFIG_SCHEMA: dict = {
    "driver": _obj(
        "WebSocket 服务",
        "OneBot v11 反向 WS 的监听服务",
        enabled=_bool("启用", "是否启用反向 WS 服务"),
        host=_str("监听地址", "服务监听 IP,默认 127.0.0.1"),
        port=_int(1, 65535, "监听端口", "反向 WS 端口,如 6199"),
    ),
    "onebot": _obj(
        "OneBot v11(反向 WS)",
        "机器人作为 WS 服务端,等待 NapCat/Lagrange 反向连接",
        host=_str("监听地址"),
        port=_int(1, 65535, "监听端口"),
        path=_str("WS 路径", "默认 /ws"),
        token=_str("Access Token"),
        self_id=_str("机器人 QQ 号"),
    ),
    "onebot_forward": _obj(
        "OneBot v11(正向 WS)",
        "机器人主动连接 OneBot 实现端的 WS 服务端",
        enabled=_bool("启用"),
        url=_str("WS 地址", "OneBot 实现端 WS 地址,如 ws://127.0.0.1:6700"),
        token=_str("Access Token"),
        self_id=_str("机器人 QQ 号"),
    ),
    "onebot_http": _obj(
        "OneBot v11(HTTP)",
        "OneBot 协议 HTTP 上报模式",
        enabled=_bool("启用"),
        host=_str("监听地址"),
        port=_int(1, 65535, "监听端口"),
        path=_str("HTTP 路径"),
        http_url=_str("上报地址", "OneBot 实现端 HTTP 上报地址"),
        token=_str("Access Token"),
        self_id=_str("机器人 QQ 号"),
    ),
    "qq_official": _obj(
        "QQ 官方机器人",
        "QQ 官方机器人接口(Webhook 模式)",
        enabled=_bool("启用"),
        host=_str("监听地址"),
        port=_int(1, 65535, "监听端口"),
        path=_str("Webhook 路径"),
        app_id=_str("App ID"),
        app_secret=_str("App Secret"),
        sign_secret=_str("签名密钥"),
    ),
    "telegram": _obj(
        "Telegram",
        "Telegram Bot(长轮询)",
        enabled=_bool("启用"),
        token=_str("Bot Token"),
        allowed_chat_ids=_list("允许的聊天 ID", "逗号分隔的聊天 ID 列表"),
        poll_timeout=_int(1, 50, "轮询超时(秒)"),
    ),
    "llm": _obj(
        "LLM(对话模型)",
        "对话模型的 Provider 配置",
        provider=_obj(
            "Provider",
            "模型提供商配置(type/model/api_key/base_url)",
            type=_str("提供商类型", "如 openai / deepseek / anthropic 等"),
            model=_str("模型名", "如 gpt-4o / deepseek-chat 等"),
            api_key=_str("API Key"),
            base_url=_str("API 地址", "留空使用默认地址"),
            max_tokens=_int(1, 200000, "最大输出 Token"),
            temperature={"type": (int, float), "min": 0, "max": 2, "description": "温度", "hint": "0-2,越高越随机"},
            fallback_providers=_list("备用 Provider", "主 Provider 失败时按顺序尝试的 Provider 配置"),
        ),
    ),
    "search": _obj(
        "联网搜索",
        "搜索工具所需的 API Key",
        tavily_key=_str("Tavily Key"),
        brave_key=_str("Brave Key"),
    ),
    "mcp": _obj(
        "MCP 服务器",
        "Model Context Protocol 外部工具服务器",
        servers=_list("服务器列表"),
    ),
    "agent": _obj(
        "Agent 引擎",
        "ReAct 循环与子代理的全局参数",
        workdir=_str("工作目录", "工具运行的工作目录"),
        max_iterations=_int(1, 64, "最大迭代轮数"),
        max_context_tokens=_int(0, 1000000, "最大上下文 Token"),
    ),
    "cron": _obj(
        "定时任务",
        "主动消息与定时任务",
        agent_enabled=_bool("启用 Agent 执行定时任务"),
    ),
    "notice": _obj(
        "通知与消息",
        "进群/退群/撤回等消息",
        welcome=_obj("欢迎消息", "新成员进群时发送",
                     enabled=_bool("启用"), text=_str("文案")),
        farewell=_obj("退群消息", "成员退群时发送",
                      enabled=_bool("启用"), text=_str("文案")),
        anti_recall=_obj("防撤回", "成员撤回时转发",
                         enabled=_bool("启用"), format=_str("转发格式")),
        friend_requests=_str("好友申请处理", "auto/ask/ignore"),
        group_requests=_str("群申请处理", "auto/ask/ignore"),
    ),
    "pipeline": _obj(
        "管道与调度",
        "消息处理管道参数",
        group_whitelist=_list("群白名单"),
        wake_words=_list("唤醒词"),
        command_prefixes=_list("命令前缀"),
        rate_limit=_obj("频率限制",
                        max_messages=_int(1, "窗口内最大消息数"),
                        window_seconds=_int(1, "窗口时长(秒)")),
        content_safety=_obj("内容安全",
                            max_length=_int(1, "单条消息最大长度")),
    ),
    "security": _obj(
        "安全",
        "权限与安全设置",
        admin_users=_list("管理员", "管理员 QQ 号或用户名"),
        super_admin_users=_list("超级管理员"),
        trusted_folders=_list("可信文件夹", "允许工具访问的路径"),
        sandbox_enabled=_bool("启用沙箱", "工具命令在沙箱中执行"),
        audit_enabled=_bool("启用审计日志"),
        pairing_enabled=_bool("启用配对"),
    ),
    "webui": _obj(
        "Dashboard",
        "Web 管理界面",
        enabled=_bool("启用"),
        host=_str("监听地址"),
        port=_int(1, 65535, "监听端口"),
        password=_str("管理密码"),
    ),
    "knowledge": _obj(
        "知识库",
        "向量知识库参数",
        embedding_model=_str("Embedding 模型"),
        embedding_provider=_str("Embedding 提供商"),
        chunk_size=_int(1, 100000, "分块大小"),
        top_k=_int(1, 100, "检索 Top-K"),
        rerank_enabled=_bool("启用重排序"),
        fallback_enabled=_bool("启用关键词回退"),
    ),
    "generation": _obj(
        "AIGC 生成",
        "图片/视频生成",
        image_provider=_str("图片生成提供商"),
        image_api_key=_str("图片生成 API Key"),
        video_provider=_str("视频生成提供商"),
        video_api_key=_str("视频生成 API Key"),
    ),
    "provider_stt": _obj(
        "语音识别(STT)",
        "语音转文字",
        type=_str("提供商类型"), model=_str("模型名"),
        api_key=_str("API Key"), base_url=_str("API 地址"),
    ),
    "provider_tts": _obj(
        "语音合成(TTS)",
        "文字转语音",
        type=_str("提供商类型"), model=_str("模型名"),
        api_key=_str("API Key"), base_url=_str("API 地址"),
    ),
    "provider_embedding": _obj(
        "向量嵌入(Embedding)",
        "文本向量化",
        type=_str("提供商类型"), model=_str("模型名"),
        api_key=_str("API Key"), base_url=_str("API 地址"),
    ),
    "provider_rerank": _obj(
        "重排序(Rerank)",
        "检索结果重排",
        type=_str("提供商类型"), model=_str("模型名"),
        api_key=_str("API Key"), base_url=_str("API 地址"),
    ),
    "sandbox": _obj(
        "沙箱",
        "命令执行沙箱",
        mode=_str("沙箱模式"), max_sessions=_int(1, 64, "最大会话数"),
        timeout=_int(1, "超时(秒)"),
    ),
    "storage": _obj(
        "存储",
        "数据存储",
        sqlmodel=_bool("启用 SQLModel"),
    ),
}


def _type_name(t) -> str:
    if t is bool:
        return "bool"
    if t is int:
        return "int"
    if t in (float, (int, float)):
        return "float"
    if t is list:
        return "list"
    return "string"


def _section_entry(section_key: str, rule: dict) -> dict:
    """把单个顶层配置 section 转为 AstrBotConfigV4 需要的 object 条目。

    items 的键为点号路径(如 ``llm.provider.type``),供前端 selector 直接
    在 config_data 上取值/写值。
    """
    items: dict = {}

    def walk(path: str, r: dict) -> None:
        t = r.get("type")
        if t is dict:
            for k, child in (r.get("children") or {}).items():
                walk(f"{path}.{k}" if path else k, child)
        else:
            items[path] = {
                "type": _type_name(t),
                "description": r.get("description", ""),
                "hint": r.get("hint", ""),
            }

    for k, child in (rule.get("children") or {}).items():
        walk(f"{section_key}.{k}", child)
    return {
        "type": "object",
        "description": rule.get("description", section_key),
        "hint": rule.get("hint", ""),
        "items": items,
    }


# 分组定义:group_key -> (name i18n key, [顶层配置 section 列表])
_CONFIG_GROUPS: dict[str, tuple[str, list[str]]] = {
    "ai_group": (
        "ai_group.name",
        ["llm", "agent", "search", "knowledge", "generation", "cron"],
    ),
    "platform_group": (
        "platform_group.name",
        ["driver", "onebot", "onebot_forward", "onebot_http", "qq_official", "telegram"],
    ),
    "provider_group": (
        "provider_group.name",
        ["provider_stt", "provider_tts", "provider_embedding", "provider_rerank"],
    ),
    "ext_group": (
        "ext_group.name",
        ["mcp", "sandbox", "storage", "notice", "pipeline"],
    ),
    "system_group": (
        "system_group.name",
        ["security", "webui"],
    ),
}


def _build_config_metadata(with_templates: bool = True) -> dict:
    """构建前端配置页使用的分组 metadata。

    Args:
        with_templates: 是否注入 platform/provider 的 ``config_template``
            (供"新增平台/新增提供商"弹窗使用)。
    """
    groups: dict = {}
    for group_key, (name_key, section_keys) in _CONFIG_GROUPS.items():
        metadata: dict = {}
        for section_key in section_keys:
            rule = _CONFIG_SCHEMA.get(section_key)
            if rule is None:
                continue
            metadata[section_key] = _section_entry(section_key, rule)
        groups[group_key] = {"name": name_key, "metadata": metadata}

    if with_templates:
        # platform 模板:供 AddNewPlatform 类型下拉(内联自 bot_service.BOT_TYPES,避免循环导入)
        platform_entry = {
            "type": "object",
            "description": "平台配置",
            "hint": "",
            "items": {},
            "config_template": _PLATFORM_TEMPLATES,
        }
        groups["platform_group"]["metadata"]["platform"] = platform_entry

        # provider 模板:供 AddNewProvider 类型下拉
        provider_entry = {
            "type": "object",
            "description": "服务提供商",
            "hint": "",
            "items": {},
            "config_template": _PROVIDER_TEMPLATES,
        }
        groups["provider_group"]["metadata"]["provider"] = provider_entry

    return groups


# 内联模板(不与 dashboard 服务循环导入)
_PLATFORM_TEMPLATES: dict = {
    "onebot": {"type": "onebot", "id": "onebot", "enable": False,
               "host": "127.0.0.1", "port": 6199, "path": "/ws", "token": "", "self_id": ""},
    "onebot_forward": {"type": "onebot_forward", "id": "onebot_forward", "enable": False,
                       "url": "ws://127.0.0.1:6700", "token": "", "self_id": ""},
    "onebot_http": {"type": "onebot_http", "id": "onebot_http", "enable": False,
                    "host": "127.0.0.1", "port": 6199, "path": "/onebot_http",
                    "http_url": "", "token": "", "self_id": ""},
    "qq_official": {"type": "qq_official", "id": "qq_official", "enable": False,
                    "host": "127.0.0.1", "port": 6199, "path": "/qq_official",
                    "app_id": "", "app_secret": "", "sign_secret": ""},
    "telegram": {"type": "telegram", "id": "telegram", "enable": False,
                 "token": "", "allowed_chat_ids": [], "poll_timeout": 30},
}

_PROVIDER_TEMPLATES: dict = {
    "llm": {"id": "llm", "type": "llm", "provider_type": "chat_completion",
            "provider": "llm", "enable": True, "model": "", "key": "", "api_base": ""},
    "stt": {"id": "stt", "type": "stt", "provider_type": "speech_to_text",
            "provider": "stt", "enable": True, "model": "", "key": "", "api_base": ""},
    "tts": {"id": "tts", "type": "tts", "provider_type": "text_to_speech",
            "provider": "tts", "enable": True, "model": "", "key": "", "api_base": ""},
    "embedding": {"id": "embedding", "type": "embedding", "provider_type": "embedding",
                  "provider": "embedding", "enable": True, "model": "", "key": "", "api_base": ""},
    "rerank": {"id": "rerank", "type": "rerank", "provider_type": "rerank",
               "provider": "rerank", "enable": True, "model": "", "key": "", "api_base": ""},
}


CONFIG_METADATA_2: dict = _build_config_metadata(with_templates=True)
CONFIG_METADATA_3: dict = _build_config_metadata(with_templates=True)
CONFIG_METADATA_3_SYSTEM: dict = _build_config_metadata(with_templates=True)


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
