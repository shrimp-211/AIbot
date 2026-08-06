"""QQ 群文化人格适配器(参照 AstrBot 人格系统 + 用户"增强 QQ 环境适配"需求)。

- 群友风 system prompt:随机表情/颜文字/轻松口吻,随会话注入
- 表情自动回应:消息含关键词 → 返回 QQ 表情 CQ 码
- 复读检测:群内同消息连续 N 次 → 参与复读
- 骰子/娱乐:随机触发(经 qq_send_dice 工具)
"""
from __future__ import annotations

import random
from collections import deque

# 群友风口吻片段,随机抽取
_CASUAL_OPENERS = ["嗯嗯", "来啦", "看到啦", "收到收到", "好嘞", "哦哦"]
_CASUAL_SUFFIX = ["~", "～", "!", "!~", "……", "~", ""]
_KAOMOJI = ["(￣▽￣)", "(｡･ω･｡)", "(>ω<)", "(≧▽≦)", "(*≧ω≦)", "(◕‿◕)"]

# 关键词 → QQ 表情 id(NapCat set_msg_emoji_like / face CQ)
_KEYWORD_EMOJI = {
    "哈哈": 21, "好笑": 21, "笑死": 21,
    "赞": 322, "牛": 322, "厉害": 322, "大佬": 322,
    "爱你": 307, "喜欢": 307, "么么": 307,
    "哭": 17, "呜呜": 17, "难受": 17,
    "再见": 6, "拜拜": 6, "溜": 6,
    "加油": 74, "冲": 74,
    "疑惑": 22, "?": 22, "？": 22,
}


class QqPersona:
    """QQ 群文化人格适配器(无状态;复读统计由调用方经 add_repeat_sample 维护)。"""

    def __init__(self, repeat_threshold: int = 3):
        self.repeat_threshold = max(2, int(repeat_threshold))
        # 每群最近消息文本(用于复读检测),容量有限防内存增长
        self._recent: dict[str, deque[str]] = {}

    # ---------- 群友风 prompt ----------

    def build_prompt(self, message_type: str) -> str:
        """生成 QQ 环境人格引导,注入 system prompt。"""
        opener = random.choice(_CASUAL_OPENERS)
        suffix = random.choice(_CASUAL_SUFFIX)
        kaomoji = random.choice(_KAOMOJI)
        if message_type == "group":
            return (
                "## QQ 群聊风格\n"
                f"- 像群友一样自然聊天,开头可带一点口语(如「{opener}」),语气轻松,避免官腔。\n"
                "- 回复保持简短(通常 2-3 行),不要输出 Markdown 标题/表格/代码块。\n"
                "- 聊到有趣的内容可以自然地接梗、附和,偶尔加个表情(但别过度)。\n"
                "- 群里有人重复同一句话多次时,可以跟着复读一句(如果合适)。\n"
                "- 需要氛围时可以提议骰子/表情互动。"
            )
        return (
            "## QQ 私聊风格\n"
            f"- 语气友好但不过度卖萌,像靠谱的私聊助手{suffix} {kaomoji}\n"
            "- 表达清晰有条理,一次说清要点,重要信息可用简单分行。"
        )

    # ---------- 表情回应 ----------

    def auto_emoji(self, text: str) -> int | None:
        """消息含关键词时返回建议的 QQ 表情 id,无命中返回 None。"""
        text = text or ""
        for kw, face_id in _KEYWORD_EMOJI.items():
            if kw in text:
                return face_id
        return None

    # ---------- 复读检测 ----------

    def add_sample(self, group_id: str, text: str) -> None:
        """登记一条群消息文本(用于复读统计)。"""
        key = str(group_id or "")
        if not key:
            return
        buf = self._recent.setdefault(key, deque(maxlen=50))
        buf.append((text or "").strip())

    def repeat_count(self, group_id: str, text: str) -> int:
        """当前消息在最近窗口中已出现的次数(含本条前)。"""
        key = str(group_id or "")
        buf = self._recent.get(key)
        if not buf:
            return 1
        t = (text or "").strip()
        return 1 + sum(1 for x in buf if x == t)

    def should_repeat(self, group_id: str, text: str) -> bool:
        """是否触发复读:同文本连续达到阈值(且文本非空、不是命令)。"""
        t = (text or "").strip()
        if not t or len(t) < 2 or t.startswith(("/", "!", "STOP", "STATUS")):
            return False
        return self.repeat_count(group_id, t) >= self.repeat_threshold

    # ---------- 统计 ----------

    def stats(self) -> dict:
        return {"groups_tracked": len(self._recent), "repeat_threshold": self.repeat_threshold}
