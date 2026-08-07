"""Agent 引擎:ReAct 循环(Think→Act→Observe)。

参考 Claude Code 的 queryLoop 与 AstrBot 的 AgentRequestSubStage:
while 循环中构建 prompt → 调用 LLM → 执行工具 → 回传结果 → 直至最终回复。
"""
from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger

from ..adapter.event import AgentEvent
from ..adapter.message import MessageChain
from .compressor import (
    compress_tool_result,
    compress_messages,
    should_compress,
    truncate_by_halving,
)

# ---------- 重复工具调用指导(参照 AstrBot _track_tool_call_streak) ----------

_REPEATED_TOOL_L1_THRESHOLD = 3
_REPEATED_TOOL_L2_THRESHOLD = 4
_REPEATED_TOOL_L3_THRESHOLD = 5
_REPEATED_TOOL_L1_TEMPLATE = (
    "\n\n[系统提示] 你已经连续 {streak} 次用相同参数调用工具 `{tool_name}`。"
    "请检查是否应换一个工具、调整参数,或直接总结当前结果。"
)
_REPEATED_TOOL_L2_TEMPLATE = (
    "\n\n[系统提示] 注意:你已经连续 {streak} 次用相同参数调用工具 `{tool_name}`。"
    "除非重复确有必要,否则请停止重复,改换工具或调整参数,或说明还缺什么信息。"
)
_REPEATED_TOOL_L3_TEMPLATE = (
    "\n\n[系统提示] 警告:你已经连续 {streak} 次用相同参数调用工具 `{tool_name}`。"
    "重复程度已很高。请仅在每次调用都明显产出新信息时继续,否则改变策略、调整参数,"
    "或向用户说明限制。"
)


def _repeated_tool_guidance(tool_name: str, streak: int) -> str:
    if streak >= _REPEATED_TOOL_L3_THRESHOLD:
        return _REPEATED_TOOL_L3_TEMPLATE.format(tool_name=tool_name, streak=streak)
    if streak >= _REPEATED_TOOL_L2_THRESHOLD:
        return _REPEATED_TOOL_L2_TEMPLATE.format(tool_name=tool_name, streak=streak)
    if streak >= _REPEATED_TOOL_L1_THRESHOLD:
        return _REPEATED_TOOL_L1_TEMPLATE.format(tool_name=tool_name, streak=streak)
    return ""


def _classify_approval(text: str) -> str | None:
    """分类工具审批回复:短文本命中允许/拒绝词,返回 approve/deny,否则 None。

    仅匹配整体较短(<16 字符)的回复,避免把普通长消息误判为审批答复。
    """
    t = text.strip().lower().rstrip("。.!！?？~～")
    if not t or len(t) > 16:
        return None
    # 否定式拒绝词优先,防止"不同意/不批准"被"同意/批准"子串误判为允许
    if any(w in t for w in ("不同意", "不批准", "不确认", "不授权", "拒绝", "取消", "不需要", "不了")):
        return "deny"
    if any(w in t for w in ("允许", "批准", "同意", "确认", "授权")):
        return "approve"
    if t in ("可以", "yes", "y", "ok", "okay", "allow", "approve", "继续", "好", "行"):
        return "approve"
    if t in ("no", "n", "deny", "不行", "不要", "别"):
        return "deny"
    return None


from .tools.base import ToolApprovalRequired, ToolContext, ToolRegistry

if TYPE_CHECKING:
    from ..providers.base import BaseProvider
    from ..security.auth import AuthManager
    from ..storage.db import JsonKV
    from ..utils.config import Config

DEFAULT_PERSONA = (
    "你是 QQ 群里的智能 AI 助手。你乐于助人、回复简洁友好,"
    "能用工具搜索信息、读写文件、执行命令、管理群聊,帮助用户高效完成任务。"
)


class AgentEngine:
    def __init__(
        self,
        provider: "BaseProvider",
        tools: ToolRegistry,
        memory: Any,
        auth: "AuthManager",
        config: "Config",
        adapter: Any,
        db: "JsonKV",
        persona_manager: Any = None,
        hooks: Any = None,
        cron_manager: Any = None,
        skills: Any = None,
        subagent_manager: Any = None,
        sqlite_store: Any = None,
        file_memory: Any = None,
        mcp_manager: Any = None,
        audit_logger: Any = None,
        usage_tracker: Any = None,
        perception: Any = None,
        knowledge: Any = None,
        generation: Any = None,
        tool_result_disk: Any = None,
        sandbox_pool: Any = None,
        interrupt: Any = None,
        followup: Any = None,
        qq_persona: Any = None,
    ):
        self.provider = provider
        self.tools = tools
        self.memory = memory
        self.auth = auth
        self.config = config
        self.adapter = adapter
        self.db = db
        self.persona_manager = persona_manager
        self.hooks = hooks
        self.cron_manager = cron_manager
        self.skills = skills
        self.subagent_manager = subagent_manager
        self.sqlite_store = sqlite_store
        self.file_memory = file_memory
        self.mcp_manager = mcp_manager
        self.audit_logger = audit_logger
        self.perception = perception
        self.knowledge = knowledge
        self.generation = generation
        self.tool_result_disk = tool_result_disk
        self.sandbox_pool = sandbox_pool
        self.interrupt = interrupt
        self.followup = followup
        self.qq_persona = qq_persona
        self._static_prompt: str | None = None
        self.messages_processed = 0  # 累计处理消息数(WebUI 统计)

        # LLM 用量统计:默认按 db 建 tracker(测试/嵌入场景未传也不阻塞)
        if usage_tracker is None:
            from .usage import UsageTracker

            usage_tracker = UsageTracker(db)
        self.usage = usage_tracker

        if self.subagent_manager is None:
            from .subagent import SubagentManager

            self.subagent_manager = SubagentManager(self.provider, self.config)

    def set_adapter(self, adapter: Any) -> None:
        """注入默认发送适配器(构造时可能尚不存在,由 main.py 启动期回填)。"""
        self.adapter = adapter

    # ---------- 运行时控制(命令体系) ----------

    @property
    def model_name(self) -> str:
        """当前生效模型名(ProviderManager 经 __getattr__ 代理到 active provider)。"""
        try:
            return self.provider.model
        except AttributeError:
            return self.config.get("llm.provider.model", "?")

    def set_model(self, model: str) -> dict:
        """运行时切换 LLM 模型(热切换, 参照 AstrBot 模型切换命令)。"""
        model = (model or "").strip()
        if not model:
            return {"error": "模型名不能为空"}
        old = self.model_name
        try:
            self.provider.set_model(model)
        except AttributeError:  # 极老的 provider 无 set_model,直接改属性
            self.provider.model = model
        self.config.set("llm.provider.model", model)
        logger.info("模型已切换: {} -> {}", old, model)
        return {"ok": True, "model": model}

    def clear_session(self, session_id: str) -> dict:
        """清空会话上下文:工作记忆 + 挂起问题 + 检查点(参照 AstrBot 清空对话)。"""
        self.memory.clear_working(session_id)
        if hasattr(self.memory, "clear_pending_question"):
            self.memory.clear_pending_question(session_id)
        if hasattr(self.memory, "clear_checkpoint"):
            self.memory.clear_checkpoint(session_id)
        return {"ok": True, "session_id": session_id}

    # ---------- 入口 ----------

    async def process(self, event: AgentEvent) -> str | None:
        """处理一条消息,返回回复文本(返回 None 表示不回复)。"""
        if event.is_stopped:
            return None

        # 新回合开始:清除该会话的中断标记
        if self.interrupt is not None:
            self.interrupt.reset(event.session_id)

        self.messages_processed += 1
        text = event.plain_text.strip()
        if not text and event.is_tome:
            text = "(用户@了你)"

        # 工具审批续接:上一轮请求权限批准,本轮检测用户的允许/拒绝(Claude Code 权限批准)
        # 放在 fast path 之前,确保审批回复优先处理。仅"明确的审批答复"或"明确中止(STOP)"
        # 才结算审批;普通消息(无关的新请求)保留挂起审批,用户仍可在后续回复中应答。
        approval_reply = False
        pending = self.auth.get_pending_tool_approval(event.user_id)
        if pending:
            decision = _classify_approval(text)
            if decision:
                approval_reply = True
                self.auth.resolve_tool_approval(event.user_id, decision == "approve")
                event.state["approval_decision"] = decision
                event.state["approval_tool"] = pending["tool"]
            elif text.strip().upper() in ("STOP", "停止", "停", "结束", "退出"):
                # 用户明确中止会话:放弃挂起的审批(避免残留到 TTL)
                self.auth.resolve_tool_approval(event.user_id, False)
            # 其余普通消息:不触碰挂起审批

        fast = self._nlu_fast_path(event, text)
        if fast is not None:
            return fast

        # 技能自动匹配:当前无激活技能时,若消息强匹配某技能描述则自动激活
        if self.skills is not None and self.skills.active(event.session_id) is None:
            matched = self.skills.auto_select(text)
            if matched is not None:
                self.skills.activate(event.session_id, matched.name)

        # 审批回复是元消息(允许/拒绝),不写入对话记忆,避免污染上下文
        if not approval_reply:
            self.memory.add_message(event.session_id, "user", text)
            await self._record_to_sqlite(event, "user", text)
        await self._load_memory_context(event, text)

        # ask_user 续接:上一轮向用户提问后,本轮注入提示并清除待答标记
        # 审批回复是元消息(允许/拒绝),不是对 ask_user 问题的回答,跳过续接
        if not approval_reply and self.memory is not None:
            pending = self.memory.get_pending_question(event.session_id)
            if pending:
                event.state["awaiting_question"] = pending.get("question", "")
                self.memory.clear_pending_question(event.session_id)

        ctx = self._build_tool_context(event)
        if self.subagent_manager is not None:
            self.subagent_manager.bind_executor(self._make_subagent_executor(ctx))
        messages = self._build_messages(event, text)
        system_prompt = self._build_system_prompt(event)

        if self.hooks:
            await self.hooks.trigger("user_prompt_submit", event=event, text=text)

        try:
            reply = await self._run_react_loop(ctx, messages, system_prompt)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("Agent 处理消息异常")
            reply = "抱歉,处理你的消息时出了点问题,请稍后再试。"

        if self.hooks:
            await self.hooks.trigger("post_agent", reply=reply or "", event=event)
        if reply:
            self.memory.add_message(event.session_id, "assistant", reply)
            await self._record_to_sqlite(event, "assistant", reply)
            await self._maybe_reflect(event)
        return reply

    async def _maybe_reflect(self, event: AgentEvent) -> None:
        """周期性自我反思:每 8 条用户消息,让 LLM 提炼该用户值得长期记住的信息。"""
        if self.sqlite_store is None:
            return
        try:
            key = f"reflect_count_{event.user_id}"
            n = int(self.db.get(key, 0) or 0) + 1
            self.db.set(key, n)
            if n % 8 != 0:
                return
            # 成本保护:两次反思至少间隔 reflect_cooldown 秒,高流量下避免频繁 LLM 调用
            cooldown = int(self.config.get("agent.reflect_cooldown", 300) or 300)
            last_key = f"reflect_last_{event.user_id}"
            last = float(self.db.get(last_key, 0) or 0)
            if time.time() - last < cooldown:
                return
            self.db.set(last_key, time.time())
            recent = await self.sqlite_store.recent(event.session_id, limit=12)
            if not recent:
                return
            conv = "\n".join(f"{m['role']}: {m['content'][:300]}" for m in recent)
            prompt = (
                "根据以下对话,提取该用户 3-5 条值得长期记住的关键信息"
                "(偏好/习惯/事实/身份/目标),用简短要点列出,不要客套:\n" + conv
            )
            result = await self.provider.chat(
                [{"role": "user", "content": prompt}],
                system_prompt="你是记忆整理助手,只输出要点。",
            )
            summary = (result.get("content") or "").strip()
            if summary:
                self.memory.record_reflection(event.user_id, summary)
                logger.info("已完成一次用户自我反思({})", event.user_id)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("自我反思失败")

    async def _record_to_sqlite(self, event: AgentEvent, role: str, content: str) -> None:
        """把消息写入 SQLite+FTS5 存储,支持跨会话搜索。"""
        if self.sqlite_store is None or not content:
            return
        try:
            await self.sqlite_store.add_message(
                session_id=event.session_id,
                role=role,
                content=content,
                user_id=event.user_id,
                group_id=event.group_id,
                platform=event.platform,
            )
        except Exception:  # noqa: BLE001
            logger.exception("SQLite 记忆写入失败")

    async def _load_memory_context(self, event: AgentEvent, text: str) -> None:
        """预取相关记忆到 event.state,供消息/Prompt 构建读取。"""
        if self.sqlite_store is not None:
            try:
                hits = await self.sqlite_store.search(text, limit=5, user_id=event.user_id)
                if hits:
                    event.state["memory_hits"] = hits
            except Exception:  # noqa: BLE001
                logger.exception("SQLite 记忆检索失败")
        if self.file_memory is not None:
            try:
                ctx_text = await self.file_memory.build_context(max_chars=6000)
                if ctx_text:
                    event.state["file_memory_context"] = ctx_text
            except Exception:  # noqa: BLE001
                logger.exception("文件记忆上下文加载失败")

    # ---------- NLU 快速路径 ----------

    def _nlu_fast_path(self, event: AgentEvent, text: str) -> str | None:
        t = text.strip().upper()
        if t in ("STOP", "停止", "停", "结束", "退出"):
            self.memory.clear_working(event.session_id)
            return "已结束当前对话上下文。"
        if t in ("HELP", "帮助", "?", "？"):
            return self._help_text()
        if t in ("STATUS", "状态"):
            return self._status_text()
        if text.startswith(("我的记忆", "我的画像")):
            profile = self.memory.get_profile(event.user_id)
            return (
                json.dumps(profile, ensure_ascii=False, indent=2)
                if profile
                else "暂无你的用户画像记录。"
            )
        return None

    _HELP_GROUPS: list[tuple[str, tuple[str, ...]]] = [
        ("🔍 搜索与网络", ("web_search", "web_fetch")),
        ("📁 文件与命令", ("file_read", "file_write", "file_edit", "glob", "grep", "bash")),
        ("🧠 知识库与记忆", ("knowledge_add", "knowledge_search", "knowledge_list", "memory_")),
        ("🖼️ 图片/媒体理解", ("vision_analyze", "ocr_extract", "audio_transcribe", "video_summarize", "document_parse", "media_analyze")),
        ("🎨 内容生成", ("image_generate", "video_generate", "tts_speak", "mixed_media")),
        ("💬 QQ 消息", ("qq_send_image", "qq_send_voice", "qq_send_like", "qq_recall")),
        ("🎉 QQ 互动", ("qq_send_poke", "qq_send_emoji", "qq_send_dice", "qq_send_music", "qq_ocr")),
        ("🛡️ QQ 群管理", ("qq_kick", "qq_mute", "qq_set_admin", "qq_essence", "qq_group_announce", "qq_group_file_list", "qq_group_info", "qq_group_list")),
        ("📋 规划与会话", ("plan", "task_", "session_", "subagent")),
        ("⏰ 其他", ("cron", "translate", "mcp_server_list", "skill_", "ask_user")),
    ]

    def _help_text(self) -> str:
        schemas = {s["name"]: s for s in self.tools.schemas()}
        lines = ["🤖 我是 QQ AI Agent,直接说需求即可。可用能力如下:", ""]
        used: set[str] = set()
        for title, prefixes in self._HELP_GROUPS:
            members = [
                s for name, s in schemas.items()
                if any(name == p or name.startswith(p) for p in prefixes) and name not in used
            ]
            if not members:
                continue
            lines.append(f"{title}")
            for s in members:
                lines.append(f"  · {s['name']} — {s.get('description', '')}")
            used.update(m["name"] for m in members)
            lines.append("")
        leftovers = [s["name"] for name, s in schemas.items() if name not in used]
        if leftovers:
            lines.append("🔧 其他")
            lines.extend(f"  · {n}" for n in sorted(leftovers))
            lines.append("")
        lines.append("输入 `STOP` 结束对话,`STATUS` 查看状态,`/help` 查看本帮助。")
        return "\n".join(lines)

    def _status_text(self) -> str:
        ptype = self.config.get("llm.provider.type", "?")
        model = self.config.get("llm.provider.model", "?")
        return (
            f"🟢 运行正常\n"
            f"模型: {model} ({ptype})\n"
            f"工具数: {len(self.tools.names())}\n"
            f"已处理消息: {self.messages_processed}\n"
            f"记忆: 三层记忆已启用"
        )

    def stats(self) -> dict[str, Any]:
        """引擎运行统计,供 WebUI 状态页展示。"""
        try:
            usage = self.usage.summary()
        except Exception:  # noqa: BLE001
            usage = {}
        return {
            "messages_processed": self.messages_processed,
            "tools": len(self.tools.names()),
            "provider": type(self.provider).__name__,
            "model": self.model_name,
            "usage": usage,
        }

    # ---------- Prompt 构建 ----------

    def _filtered_schemas(self, session_id: str) -> list[dict[str, Any]]:
        """按技能白名单 / 人格白名单 / plan_only 只读门禁过滤工具 schema。

        技能与人格白名单都非空时取交集;plan_only 已由 skills.tool_filter 收敛为只读集。
        """
        allowlist = self.skills.tool_filter(session_id) if self.skills else None
        persona_allow = (
            self.persona_manager.get_tool_allowlist(session_id)
            if self.persona_manager
            else None
        )
        if allowlist is not None and persona_allow is not None:
            allowlist = [n for n in allowlist if n in persona_allow]
        elif allowlist is None:
            allowlist = persona_allow
        # 全局工具允许列表(agent.tools_allowlist,空=全部):收窄部署暴露面
        global_allow = self.config.get("agent.tools_allowlist", []) or []
        if global_allow:
            base = allowlist if allowlist is not None else self.tools.names()
            allowlist = [n for n in base if n in global_allow]
        if allowlist is None:
            schemas = self.tools.schemas()
        else:
            schemas = [s for s in self.tools.schemas() if s["name"] in allowlist]
        # 按 Provider 模态能力过滤(如文本模型剔除 vision/audio 工具)
        from ..providers.modalities import filter_tools_for_modalities

        return filter_tools_for_modalities(schemas, getattr(self.provider, "modalities", None))

    def _build_static_prompt(self) -> str:
        if self._static_prompt is None:
            lines = [
                "以下是你可以使用的工具(工具名: 用途):",
                "",
            ]
            for s in self._filtered_schemas(None):
                props = s.get("parameters", {}).get("properties", {})
                param_desc = (
                    ", ".join(f"{k}({v.get('type', '')})" for k, v in props.items())
                    if props
                    else "无"
                )
                lines.append(f"- {s['name']}: {s.get('description', '')} | 参数: {param_desc}")
            lines.append("")
            lines.append("使用工具时:严格按参数格式调用;工具返回结果后继续思考,直到完成任务。")
            self._static_prompt = "\n".join(lines)
        return self._static_prompt

    def _reply_guidance_prompt(self, event: AgentEvent) -> str:
        """QQ 环境人类友好回复引导(参照 AstrBot 行为约束):自然口吻 + 富媒体主动发送。"""
        guidance = [
            "## 回复风格(QQ 环境)",
            "- 像群友一样自然聊天,回复简洁友好,避免教科书式长文与生硬列表。",
            "- 群聊中可适度用表情/颜文字活跃气氛;私聊保持耐心与专注。",
            "- 生成了图片/语音/视频后,必须调用对应的 qq_send_image / qq_send_voice 工具发到当前会话,并简短说明。",
            "- 收到语音/图片消息时,先用 audio_transcribe / vision_analyze 理解内容再回应。",
            "- 检索到知识库/记忆后,直接以自然语言回答,不要展示内部 JSON 结构。",
            "- 不确定或工具失败时如实说明,不要编造结果。",
        ]
        if event.message_type == "group":
            guidance.append("- 群聊回复保持简短(通常 2-3 行),多人语境下点明你回应的是谁。")
        else:
            guidance.append("- 私聊可以更详细,一次说清要点。")
        if event.platform == "qq":
            # QQ 分段发送引导(参照流式体验):自然分点/分段便于逐条发送。
            # 优化:不过度切碎(避免一句一行),相关要点聚合到同一段,代码块/列表保持完整。
            guidance.append(
                "- QQ 回复建议自然分点或分段表达(每个要点独立一行或一段),"
                "便于逐条发送呈现流式效果;但避免过度切碎,相关内容应聚合,代码块与列表保持完整。"
            )
        return "\n".join(guidance)

    def _build_system_prompt(self, event: AgentEvent) -> str:
        static = self._build_static_prompt()
        persona = self._persona_prompt(event)
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S %A")
        role = self.auth.get_role_level(event.user_id, event.group_id)
        profile = self.memory.get_profile(event.user_id)
        parts = [
            persona,
            static,
            self._reply_guidance_prompt(event),
        ]
        if self.qq_persona is not None:
            parts.append(self.qq_persona.build_prompt(event.message_type))
        parts += [
            f"\n当前时间: {now}",
            f"当前会话平台: {event.platform} | 消息类型: {'群聊' if event.message_type == 'group' else '私聊'}",
            f"当前用户角色等级: {role} (7=超级管理员,4=管理员,1=普通用户)",
            f"用户画像: {json.dumps(profile, ensure_ascii=False) if profile else '暂无'}",
        ]
        auto = self.memory.get_auto_memory(event.user_id)
        if auto:
            parts.append("历史记忆: " + "；".join(auto))
        reflections = self.memory.get_reflections(event.user_id, limit=3)
        if reflections:
            parts.append("用户认知总结: " + "；".join(reflections))
        fm_ctx = event.state.get("file_memory_context")
        if fm_ctx:
            parts.append(f"\n长期记忆文件(可参考):\n{fm_ctx}")
        skill_prompt = self._skills_prompt(event.session_id)
        if skill_prompt:
            parts.append(skill_prompt)
        return "\n".join(parts)

    def _skills_prompt(self, session_id: str) -> str:
        """构建技能相关提示:可用技能列表 + 当前激活技能指令。"""
        if self.skills is None:
            return ""
        lines: list[str] = []
        skills = self.skills.all()
        if skills:
            lines.append("\n可用技能(用 skill_list 查看, skill_use 激活):")
            lines.append("、" .join(f"{s.name}({s.description})" for s in skills))
        active = self.skills.active(session_id)
        if active:
            lines.append(f"\n=== 当前激活技能: {active.name} ===\n{active.content}")
            lines.append("激活技能后,请严格遵循上述技能指令,并优先使用其白名单内工具。")
        return "\n".join(lines)

    def _persona_prompt(self, event: AgentEvent) -> str:
        if self.persona_manager is not None:
            return self.persona_manager.get_prompt(event.session_id)
        return DEFAULT_PERSONA

    def _build_messages(self, event: AgentEvent, text: str) -> list[dict]:
        session_id = event.session_id
        messages: list[dict] = []
        # 人格 begin_dialogs(参照 AstrBot):切换人格后首轮注入示例对话展示语言风格
        if self.persona_manager is not None:
            dialogs = self.persona_manager.get_begin_dialogs(session_id)
            if dialogs:
                for d in dialogs:
                    if isinstance(d, dict) and d.get("role") and d.get("content"):
                        messages.append({"role": d["role"], "content": d["content"]})
        # FTS5 检索的相关历史(跨会话)
        hits = event.state.get("memory_hits")
        if hits:
            lines = [f"- {h['role']}: {h['content'][:200]}" for h in hits]
            messages.append(
                {
                    "role": "system",
                    "content": "根据历史对话检索到的相关片段,可辅助回答:\n" + "\n".join(lines),
                }
            )
        # 短期记忆(跨会话,作为历史)
        for m in self.memory.get_episodic(session_id, limit=8):
            messages.append({"role": m["role"], "content": m["content"]})
        # 工作记忆(当前会话)
        for m in self.memory.get_working(session_id):
            messages.append({"role": m["role"], "content": m["content"]})
        # ask_user 续接
        if event.state.get("awaiting_question"):
            messages.append(
                {
                    "role": "system",
                    "content": (
                        f"你之前问过用户: {event.state['awaiting_question']}。"
                        "用户这次的回复很可能回答了该问题,请继续完成之前的任务。"
                    ),
                }
            )
        # 工具审批续接:批准后指示重新调用工具,拒绝后要求调整方案
        approval_decision = event.state.get("approval_decision")
        approval_tool = event.state.get("approval_tool")
        if approval_decision and approval_tool:
            if approval_decision == "approve":
                content = (
                    f"你之前请求执行工具 **{approval_tool}** 并等待用户审批,"
                    "用户已批准。请立即重新调用该工具完成之前的任务。"
                )
            else:
                content = (
                    f"你之前请求执行工具 **{approval_tool}**,用户已拒绝。"
                    "请不要调用该工具,调整方案或向用户说明原因。"
                )
            messages.append({"role": "system", "content": content})
        else:
            messages.append({"role": "user", "content": text})
        return messages

    # ---------- ReAct 循环 ----------

    async def _send_progress(self, ctx: ToolContext, text: str) -> None:
        """私聊/WebUI 会话发送处理进度指示(参照 AstrBot 思考中指示器)。

        群聊不发送,避免刷屏;WebUI 与 QQ 私聊均为 private 事件。
        """
        if ctx.event.message_type != "private":
            return
        try:
            await ctx.event.reply(text)
        except Exception:  # noqa: BLE001
            logger.exception("发送进度指示失败")

    async def _emit_stream(self, ctx: ToolContext, content: str, reasoning: str) -> None:
        """把 LLM 流式 delta 推给客户端(WebUI 经 event._stream_callback)。

        content=文本增量;reasoning=思考增量。无回调(QQ/普通会话)时静默忽略。
        """
        cb = getattr(ctx.event, "_stream_callback", None)
        if cb is None:
            return
        try:
            await cb(content, reasoning)
        except Exception:  # noqa: BLE001
            logger.debug("流式 delta 推送失败")

    async def _run_react_loop(self, ctx: ToolContext, messages: list[dict], system_prompt: str) -> str:
        schemas = self._filtered_schemas(ctx.event.session_id)
        max_iterations = int(self.config.get("agent.max_iterations", 8) or 8)
        # 动态上下文窗口:默认取 provider 按模型推断的窗口,配置可显式覆盖
        max_context = int(self.provider.context_window or 128000)
        configured = int(self.config.get("agent.max_context_tokens", 0) or 0)
        if configured > 0:
            max_context = configured
        last_tool_name: str | None = None
        last_tool_args: dict | None = None
        same_tool_streak = 0
        thinking_sent = False
        # 空输出重试计数(参照 AstrBot tool_loop_agent_runner):LLM 无内容且无工具调用时指数退避重试
        empty_retries = 0
        _EMPTY_OUTPUT_MAX_RETRIES = 3
        trusted_tokens = 0  # 上一轮 LLM 实际报告的输入 token(可信用量)
        for _ in range(max_iterations):
            if not thinking_sent:
                # 进入处理即提示"思考中"(仅私聊/WebUI,只发一次避免刷屏)
                await self._send_progress(ctx, "⏳ 思考中...")
                thinking_sent = True
            if should_compress(messages, max_context, trusted_tokens=trusted_tokens):
                if self.hooks:
                    await self.hooks.trigger("pre_compaction", messages=len(messages))
                logger.info("触发上下文压缩(ReAct 循环内)")
                messages = await compress_messages(
                    self.provider, messages, max_context,
                    keep_recent_ratio=float(self.config.get("agent.keep_recent_ratio", 0.15) or 0.15),
                )
                if should_compress(messages, max_context):
                    # 摘要后仍超限:减半兜底(参照 AstrBot ContextManager 末级检查)
                    logger.info("压缩后仍超限,减半兜底截断")
                    messages = truncate_by_halving(messages)
                if self.hooks:
                    await self.hooks.trigger("post_compaction", messages=len(messages))
            if self.hooks:
                await self.hooks.trigger("pre_llm_request", messages=len(messages), tools=len(schemas))
            # 流式调用(参照 Claude Code/AstrBot 流式):逐 delta 经事件回调推给客户端
            result: dict[str, Any] = {}
            async for delta in self.provider.chat_stream(
                messages, system_prompt=system_prompt, tools=schemas
            ):
                if delta.get("type") == "delta":
                    await self._emit_stream(
                        ctx, delta.get("content") or "", delta.get("reasoning") or ""
                    )
                elif delta.get("type") == "done":
                    result = delta
                    break
            if not result:
                result = {"content": "", "tool_calls": [], "usage": {}, "thinking": ""}
            if self.hooks:
                await self.hooks.trigger("post_llm_response", content=result.get("content", "")[:200])
            # 记录 LLM 实际输入 token 作为可信用量(供下轮压缩判断,参照 AstrBot trusted_token_usage)
            _usage = result.get("usage") or {}
            trusted_tokens = int(_usage.get("prompt_tokens", 0) or 0)
            # 记录 token 用量与估算成本(usage 缺失如 mock 时自动跳过)
            self.usage.record(
                _usage,
                user_id=ctx.event.user_id,
                model=self.model_name,
            )
            content = (result.get("content") or "").strip()
            tool_calls = result.get("tool_calls") or []

            if not tool_calls:
                if content:
                    return content
                # 空输出重试(参照 AstrBot):无内容且无工具调用,指数退避重试,避免"完成。"死胡同
                if empty_retries >= _EMPTY_OUTPUT_MAX_RETRIES:
                    return "抱歉,我没能生成有效回复,请换一种问法试试。"
                empty_retries += 1
                logger.warning("LLM 返回空输出(无内容无工具调用),{}s 后重试({}/{})",
                               2 ** empty_retries, empty_retries, _EMPTY_OUTPUT_MAX_RETRIES)
                await asyncio.sleep(2 ** empty_retries)
                continue

            empty_retries = 0  # 拿到有效响应,重置重试计数
            # follow-up 通知(参照 AstrBot FOLLOW_UP_NOTICE_TEMPLATE):工具执行期间收到的新消息
            followup_notice = ""
            if self.followup is not None:
                pending = self.followup.drain(ctx.event.session_id)
                if pending:
                    lines = "\n".join(f"{i}. {m}" for i, m in enumerate(pending, 1))
                    followup_notice = (
                        "\n\n[SYSTEM NOTICE] 工具执行期间用户发来了新消息,"
                        f"请在后续回复中优先处理这些新指令:\n{lines}"
                    )

            messages.append(
                {
                    "role": "assistant",
                    "content": content or None,
                    "tool_calls": [
                        {
                            "id": tc["id"],
                            "type": "function",
                            "function": {
                                "name": tc["name"],
                                "arguments": json.dumps(tc.get("arguments", {}), ensure_ascii=False),
                            },
                        }
                        for tc in tool_calls
                    ],
                }
            )

            async def _run_one(tc: dict) -> str:
                name = tc.get("name", "")
                # 中断检查:用户已请求中断则停止继续调用工具
                if self.interrupt is not None and self.interrupt.is_interrupted(ctx.event.session_id):
                    return "已收到中断请求,停止执行工具。"
                # 工具执行前提示进度(仅私聊/WebUI)
                await self._send_progress(ctx, f"🔧 正在执行 `{name}`...")
                output = await self._execute_tool(ctx, tc)
                # 工具调用结果回调(WebUI 渲染工具调用卡片,参照 AstrBot ToolCallCard)
                tcb = getattr(ctx.event, "_tool_callback", None)
                if tcb is not None:
                    try:
                        await tcb(name, tc.get("arguments") or {}, (output or "")[:500])
                    except Exception:  # noqa: BLE001
                        pass
                if self.hooks:
                    await self.hooks.trigger(
                        "post_tool_use", tool_name=name, output=output
                    )
                return output

            # 并行执行多个工具调用(参考 Claude Code/Codex 的并行工具)
            # 分批控制并发上限,避免一次 20 个工具同时发起子进程/HTTP 请求
            max_parallel = int(self.config.get("agent.max_parallel_tools", 4) or 4)
            if len(tool_calls) > 1:
                outputs: list[str] = []
                for i in range(0, len(tool_calls), max_parallel):
                    batch = tool_calls[i : i + max_parallel]
                    outputs.extend(await asyncio.gather(*(_run_one(tc) for tc in batch)))
            else:
                outputs = [await _run_one(tool_calls[0])]

            # 一次性把全部工具结果追加进消息,保证 assistant(tool_calls) 与 tool 结果严格配对。
            # 否则任一个 ask_user/审批挂起提前 return,会丢弃其余并行调用的结果,
            # 下一轮历史变成"assistant 声明 N 个 tool_calls 却只有 <N 个 tool 结果",各 provider 均拒绝。
            any_awaiting = False
            followup_injected = False
            for tc, output in zip(tool_calls, outputs):
                if self._tool_awaiting(output):
                    any_awaiting = True
                    messages.append(
                        {"role": "tool", "tool_call_id": tc["id"], "content": output}
                    )
                    continue
                if followup_notice and not followup_injected:
                    output = output + followup_notice
                    followup_injected = True
                # 重复工具调用检测:连续相同(名+参数)达到阈值时注入分级指导
                name = tc.get("name", "")
                args = tc.get("arguments") or {}
                if name == last_tool_name and args == last_tool_args:
                    same_tool_streak += 1
                else:
                    last_tool_name = name
                    last_tool_args = args
                    same_tool_streak = 1
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": output + _repeated_tool_guidance(name, same_tool_streak),
                    }
                )
            # 工具图片回显:把本轮工具返回的图片注入上下文供多模态模型查看
            pending_imgs = ctx.extra.pop("_tool_images", None) if ctx.extra else None
            if pending_imgs:
                await self._append_tool_images(messages, pending_imgs)
            if any_awaiting:
                # ask_user/审批挂起:消息历史已完整(所有 tool_calls 都有对应 tool 结果),
                # 等待用户回复后再继续(下一轮进入审批续接)
                return None

        # 达到最大迭代次数(参照 AstrBot MAX_STEPS_REACHED_PROMPT):注入"停止调用工具并总结"提示,
        # 移除工具(强制直接回复),再执行最后一步,避免循环戛然而止。
        logger.warning("Agent 达到最大迭代次数({}),注入总结提示", max_iterations)
        messages.append(
            {
                "role": "user",
                "content": (
                    "已达到最大工具调用次数。请停止调用任何工具,"
                    "基于已获得的信息总结任务进展与结论,直接回复用户。"
                ),
            }
        )
        result = await self.provider.chat(messages, system_prompt=system_prompt, tools=None)
        self.usage.record(result.get("usage"), user_id=ctx.event.user_id, model=self.model_name)
        content = (result.get("content") or "").strip()
        return content if content else "已达到最大工具调用次数,任务完成。"

    def _filter_tool_args(self, name: str, args: dict) -> dict:
        """过滤工具参数:只保留工具 schema 声明接受的参数(参照 AstrBot valid_params)。

        忽略多余参数并记日志,防止 LLM 误传未声明参数导致工具 TypeError。
        """
        if not args:
            return args
        try:
            schema = next((s for s in self.tools.schemas() if s.get("name") == name), None)
            if not schema:
                return args
            props = schema.get("parameters", {}).get("properties", {})
            if not props:
                return args
            extra = set(args) - set(props)
            if extra:
                logger.warning("工具 {} 忽略非期望参数: {}", name, sorted(extra))
                return {k: v for k, v in args.items() if k in props}
        except Exception:  # noqa: BLE001
            pass
        return args

    @staticmethod
    def _tool_awaiting(output: str) -> bool:
        """检测工具返回的 awaiting 标记(ask_user / 工具审批),用于终止当前 ReAct 回合。"""
        if not output:
            return False
        try:
            data = json.loads(output)
        except (json.JSONDecodeError, TypeError):
            return False
        return isinstance(data, dict) and data.get("status") in ("awaiting", "awaiting_approval")

    async def _execute_tool(self, ctx: ToolContext, tool_call: dict) -> str:
        name = tool_call.get("name", "")
        args = tool_call.get("arguments", {}) or {}
        if not isinstance(args, dict):
            args = {}
        # 参数过滤(参照 AstrBot valid_params):只传工具声明接受的参数,忽略多余参数防 TypeError
        args = self._filter_tool_args(name, args)
        role_level = self.auth.get_role_level(ctx.event.user_id, ctx.event.group_id)

        if self.hooks:
            decision = await self.hooks.trigger("pre_tool_use", tool_name=name, args=args)
            if decision == "block":
                return "该工具调用已被策略阻止"

        # 一次性授权:用户已批准执行该工具(交互式审批),跳过权限检查(按会话限定)
        force = self.auth.is_tool_allowed(ctx.event.user_id, name, ctx.event.session_id)
        # 工具执行超时(参照 AstrBot tool_call_timeout):防止工具挂起阻塞整个 Agent 循环
        tool_timeout = int(self.config.get("agent.tool_call_timeout", 60) or 60)
        try:
            result = await asyncio.wait_for(
                self.tools.execute(name, role_level, ctx, _skip_permission=force, **args),
                timeout=tool_timeout,
            )
        except ToolApprovalRequired as exc:
            return await self._request_tool_approval(ctx, exc)
        except PermissionError as exc:
            logger.warning(f"权限拦截工具 {name}: {exc}")
            return f"权限不足: {exc}"
        except asyncio.TimeoutError:
            logger.warning(f"工具 {name} 执行超时({tool_timeout}s)")
            return f"工具 {name} 执行超时(>{tool_timeout}s),已中断。"
        except Exception:  # noqa: BLE001
            logger.exception(f"工具 {name} 执行出错")
            # 不向模型泄漏异常细节(CLAUDE.md 安全规范 3)
            return "工具执行出错,请查看服务端日志"

        # 工具图片回显(参照 AstrBot tool_image_cache):检测返回的本地图片路径,后续注入上下文
        img_paths = self._extract_tool_images(result)
        if img_paths:
            ctx.extra.setdefault("_tool_images", []).extend(img_paths)

        raw = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
        # 超大结果落盘:超过阈值时全文存盘,向模型返回引用标记(防上下文膨胀)
        if self.tool_result_disk is not None:
            raw = await self.tool_result_disk.store(raw, name)
        return compress_tool_result(raw)

    @staticmethod
    def _extract_tool_images(result: Any) -> list[str]:
        """从工具返回 dict 中提取本地图片路径(path/image/file 等键)。"""
        if not isinstance(result, dict):
            return []
        paths: list[str] = []
        for key in ("path", "image", "image_path", "file", "image_url"):
            v = result.get(key)
            if (
                isinstance(v, str)
                and v.lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".webp"))
                and Path(v).is_file()
            ):
                paths.append(v)
        return paths

    async def _append_tool_images(self, messages: list[dict], paths: list[str]) -> None:
        """把工具返回的图片以 user 消息注入上下文,供多模态模型直接查看(参照 AstrBot)。

        模型不支持图像输入时跳过;上限 3 张防上下文膨胀。
        """
        try:
            from ..providers.modalities import MODALITY_IMAGE

            if MODALITY_IMAGE not in getattr(self.provider, "modalities", ()):
                return
            from .tool_image_cache import ToolImageCache

            cache = getattr(self, "tool_image_cache", None)
            if cache is None:
                cache = ToolImageCache()
                self.tool_image_cache = cache
            blocks: list[dict] = [{"type": "text", "text": "[工具返回的图片,请查看画面并据此继续]"}]
            is_anthropic = getattr(self.provider, "image_block_format", "openai") == "anthropic"
            for p in paths[:3]:
                b64, mime = cache.get_image_base64(p)
                if not b64:
                    continue
                if is_anthropic:
                    blocks.append(
                        {"type": "image", "source": {"type": "base64", "media_type": mime, "data": b64}}
                    )
                else:
                    blocks.append(
                        {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}
                    )
            if len(blocks) > 1:
                messages.append({"role": "user", "content": blocks})
                logger.info("已注入 {} 张工具图片到上下文供模型查看", len(blocks) - 1)
        except Exception as exc:  # noqa: BLE001
            logger.debug("工具图片回显失败: {}", exc)

    async def _request_tool_approval(self, ctx: ToolContext, exc: ToolApprovalRequired) -> str:
        """工具触发 ASK:向用户发送审批请求并挂起当前回合(Claude Code 权限批准)。

        记录待审批请求,用户下一条回复进入审批续接(process 中处理)。
        """
        from ..adapter.message import escape_cq

        user_id = ctx.event.user_id
        args_preview = json.dumps(exc.args, ensure_ascii=False)[:200]
        group_hint = "\n(群聊中请 @我 后回复)" if ctx.event.message_type == "group" else ""
        msg = (
            "🔐 需要你的授权:\n"
            f"Agent 想执行工具 **{escape_cq(exc.tool_name)}**\n"
            f"参数: `{escape_cq(args_preview)}`\n"
            "回复【允许】继续,或【拒绝】取消。"
            + group_hint
        )
        try:
            await ctx.event.reply(msg)
        except Exception:  # noqa: BLE001
            # 审批请求未送达:不挂起审批,避免用户从未看到请求却被静默批准
            logger.exception("发送审批请求失败")
            return "审批请求发送失败,请稍后再试。"
        record = self.auth.request_tool_approval(
            user_id, exc.tool_name, exc.args, session_id=ctx.event.session_id
        )
        logger.info("工具 {} 请求审批(user={}, ts={:.0f})", exc.tool_name, user_id, record["ts"])
        return json.dumps(
            {
                "status": "awaiting_approval",
                "tool": exc.tool_name,
                "message": "已向用户发送工具审批请求,等待用户允许/拒绝。",
            },
            ensure_ascii=False,
        )

    def _make_subagent_executor(self, ctx: ToolContext):
        """构造子代理工具执行器(复用主代理的权限/异常/压缩逻辑)。"""

        async def executor(name: str, args: dict, tool_filter: list[str] | None = None) -> str:
            if tool_filter and name not in tool_filter:
                return f"工具 {name} 不在子代理白名单内"
            return await self._execute_tool(ctx, {"name": name, "arguments": args})

        return executor

    # ---------- 工具上下文 ----------

    def _build_tool_context(self, event: AgentEvent) -> ToolContext:
        return ToolContext(
            event=event,
            adapter=self.adapter,
            auth=self.auth,
            config=self.config,
            db=self.db,
            memory=self.memory,
            persona_manager=self.persona_manager,
            cron_manager=self.cron_manager,
            skills=self.skills,
            subagent_manager=self.subagent_manager,
            extra={
                "loop": asyncio.get_running_loop(),
                "sqlite_store": self.sqlite_store,
                "file_memory": self.file_memory,
                "tool_registry": self.tools,
                "mcp_manager": self.mcp_manager,
                "audit_logger": self.audit_logger,
                "perception": self.perception,
                "knowledge": self.knowledge,
                "generation": self.generation,
                "tool_result_disk": self.tool_result_disk,
                "sandbox_pool": self.sandbox_pool,
                "interrupt": self.interrupt,
                "followup": self.followup,
            },
        )
