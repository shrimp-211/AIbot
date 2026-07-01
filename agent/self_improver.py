"""Agent 自我改进循环 — 自动评估、学习、优化.

参考 RLHF 但完全自动化，无需人工标注:
1. 自动评估: 回复后自我打分 (相关性、完整性、准确性、安全性)
2. 用户反馈学习: 从用户反应学习 (感谢=好，纠正=需要改进)
3. Prompt 自动优化: 根据效果统计数据自动调整 System Prompt
4. A/B 测试: 对比不同策略的效果
5. 能力退化检测: 定期自测发现退化
"""

from __future__ import annotations

import json
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger


@dataclass
class InteractionRecord:
    """单次交互记录."""
    session_id: str
    user_id: str
    user_message: str
    agent_reply: str
    timestamp: float = field(default_factory=time.time)
    feedback: str = ""           # positive | negative | neutral
    self_score: float = 0.0      # Agent 自评 (0.0-1.0)
    metrics: dict = field(default_factory=dict)  # 详细指标
    notes: str = ""


class SelfImprover:
    """Agent 自我改进引擎.

    自动学习模式:
    - evaluate: 自我评估回复质量
    - detect_feedback: 从用户消息检测隐式反馈
    - optimize: 根据积累的数据优化 System Prompt
    - detect_degradation: 检测能力退化
    """

    def __init__(self, provider=None, engine=None):
        self._provider = provider
        self._engine = engine
        self._history: list[InteractionRecord] = []
        self._stats: dict[str, dict] = defaultdict(lambda: {"count": 0, "avg_score": 0.0})
        self._path = Path("data/improvement")
        self._path.mkdir(parents=True, exist_ok=True)

        self._load()

    def set_provider(self, provider) -> None:
        self._provider = provider

    def set_engine(self, engine) -> None:
        self._engine = engine

    # ---- 自我评估 ----

    async def evaluate(self, session_id: str, user_id: str, user_message: str, agent_reply: str) -> InteractionRecord:
        """评估自己的回复质量."""
        record = InteractionRecord(
            session_id=session_id,
            user_id=user_id,
            user_message=user_message,
            agent_reply=agent_reply,
        )

        # 快速自评 (基于规则)
        score = 0.5
        metrics = {}

        # 相关性: 回复是否与问题相关
        relevance = self._check_relevance(user_message, agent_reply)
        metrics["relevance"] = relevance

        # 完整性: 回复长度是否合理
        if 20 < len(agent_reply) < 800:
            metrics["completeness"] = 1.0
        elif len(agent_reply) >= 800:
            metrics["completeness"] = 0.7  # 过长
        else:
            metrics["completeness"] = 0.3  # 过短

        # 安全性: 是否包含可疑内容
        unsafe_keywords = ["密码", "password", "token", "secret", "api_key"]
        has_unsafe = any(kw in agent_reply.lower() for kw in unsafe_keywords)
        metrics["safety"] = 0.0 if has_unsafe else 1.0

        # 是否有工具调用 (更可靠)
        metrics["error_free"] = 0.7 if "错误" not in agent_reply else 0.3
        metrics["error_free"] = 1.0 if "抱歉" not in agent_reply else metrics["error_free"]

        # 综合评分
        score = sum(metrics.values()) / len(metrics) if metrics else 0.5
        record.self_score = score
        record.metrics = metrics

        # 保存
        self._history.append(record)
        if len(self._history) > 1000:
            self._history = self._history[-1000:]

        # 更新统计
        self._update_stats(record)
        self._save()

        if score < 0.4:
            record.notes = "低质量回复，建议优化"
            logger.debug(f"自评低分 ({score:.2f}): {user_message[:50]}...")

        return record

    # ---- 用户反馈检测 ----

    @staticmethod
    def detect_feedback(user_message: str) -> str:
        """从用户消息检测隐式反馈.

        Returns:
            "positive" | "negative" | "neutral"
        """
        msg_lower = user_message.lower().strip()

        # Positive indicators
        positive = ["谢谢", "感谢", "很好", "good", "thanks", "不错", "明白了", "懂了", "got it"]
        for word in positive:
            if word in msg_lower:
                return "positive"

        # Negative indicators
        negative = [
            "不对", "不是这样", "错误", "错了", "wrong", "incorrect",
            "不对", "重新", "再说一遍", "没听懂", "这个不对",
        ]
        for word in negative:
            if word in msg_lower:
                return "negative"

        return "neutral"

    # ---- 策略优化 ----

    async def optimize_system_prompt(self) -> dict:
        """基于历史数据优化 System Prompt.

        分析过去 100 条交互，找出:
        - 高频失败场景
        - 有效策略
        - 推荐的 prompt 调整
        """
        if len(self._history) < 10:
            return {"message": "交互数据不足，需要至少 10 条记录"}

        recent = self._history[-100:]

        # 统计
        avg_score = sum(r.self_score for r in recent) / len(recent)
        low_quality = [r for r in recent if r.self_score < 0.4]
        high_quality = [r for r in recent if r.self_score > 0.8]

        suggestions = []

        # 低频度 → 建议更简洁
        if avg_score < 0.5 and low_quality:
            suggestions.append("提高回复的准确性和相关性")
            # 分析低质回复的共性
            common_metrics = defaultdict(list)
            for record in low_quality:
                for k, v in record.metrics.items():
                    common_metrics[k].append(v)
            for metric, values in common_metrics.items():
                avg = sum(values) / len(values)
                if avg < 0.3:
                    suggestions.append(f"改善 '{metric}': 当前平均 {avg:.2f}")

        # 高频度 → 保持策略
        if high_quality:
            success_rate = len(high_quality) / len(recent)
            if success_rate > 0.7:
                suggestions.append("当前策略表现良好，保持")

        result = {
            "avg_score": round(avg_score, 2),
            "total_interactions": len(self._history),
            "recent_analyzed": len(recent),
            "low_quality_count": len(low_quality),
            "high_quality_count": len(high_quality),
            "suggestions": suggestions,
        }

        # 如果分数明显下降，触发警报
        old_recent = self._history[-200:-100] if len(self._history) >= 200 else []
        if old_recent:
            old_avg = sum(r.self_score for r in old_recent) / len(old_recent)
            if avg_score < old_avg - 0.1:
                result["degradation_alert"] = True
                result["degradation_message"] = (
                    f"能力疑似退化: {old_avg:.2f} → {avg_score:.2f}"
                )

        logger.info(f"自我优化分析: avg_score={avg_score:.2f}, suggestions={len(suggestions)}")
        return result

    # ---- 辅助方法 ----

    @staticmethod
    def _check_relevance(question: str, answer: str) -> float:
        """检查回复相关性."""
        q_words = set(question)
        a_words = set(answer)
        if not q_words:
            return 0.5
        overlap = q_words & a_words
        return min(1.0, len(overlap) / len(q_words) * 2)

    def _update_stats(self, record: InteractionRecord) -> None:
        """更新用户/会话维度的统计."""
        key = record.session_id
        s = self._stats[key]
        s["count"] += 1
        s["avg_score"] = (s["avg_score"] * (s["count"] - 1) + record.self_score) / s["count"]

    # ---- 持久化 ----

    def _save(self) -> None:
        data = {
            "history": [
                {
                    "session_id": r.session_id,
                    "user_id": r.user_id,
                    "user_message": r.user_message[:200],
                    "agent_reply": r.agent_reply[:200],
                    "timestamp": r.timestamp,
                    "feedback": r.feedback,
                    "self_score": r.self_score,
                    "metrics": r.metrics,
                    "notes": r.notes,
                }
                for r in self._history[-200:]  # 只保留最近 200 条
            ],
            "stats": dict(self._stats),
        }
        (self._path / "data.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2)
        )

    def _load(self) -> None:
        data_file = self._path / "data.json"
        if data_file.exists():
            try:
                data = json.loads(data_file.read_text())
                for r in data.get("history", []):
                    record = InteractionRecord(**r)
                    self._history.append(record)
                self._stats = defaultdict(lambda: {"count": 0, "avg_score": 0.0}, data.get("stats", {}))
            except (json.JSONDecodeError, OSError):
                pass

    def get_stats(self) -> dict:
        """获取学习统计."""
        return {
            "total_interactions": len(self._history),
            "avg_score": (
                sum(r.self_score for r in self._history) / len(self._history)
                if self._history else 0.0
            ),
            "positive_feedback": sum(1 for r in self._history if r.feedback == "positive"),
            "negative_feedback": sum(1 for r in self._history if r.feedback == "negative"),
            "active_sessions": len(self._stats),
        }
