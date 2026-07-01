"""输入安全过滤 — Prompt Injection 防护."""

import re


class InputSanitizer:
    """输入过滤与消毒."""

    # 已知的提示注入模式
    INJECTION_PATTERNS = [
        r"ignore\s+(all\s+)?(previous|above|prior)\s+(instructions?|prompts?)",
        r"forget\s+(all\s+)?(previous|above|prior)\s+(instructions?|prompts?)",
        r"you\s+are\s+now\s+(a\s+)?\w+",
        r"\[system\]",
        r"\[/system\]",
        r"<\|im_start\|>",
        r"<\|im_end\|>",
    ]

    @classmethod
    def check_injection(cls, text: str) -> bool:
        """检查是否包含提示注入模式.

        Returns:
            True 如果检测到注入
        """
        text_lower = text.lower()
        for pattern in cls.INJECTION_PATTERNS:
            if re.search(pattern, text_lower, re.IGNORECASE):
                return True
        return False

    @classmethod
    def sanitize(cls, text: str) -> str:
        """过滤危险内容."""
        # 移除 null 字节
        text = text.replace("\x00", "")
        # 移除 ANSI 转义序列
        text = re.sub(r"\x1b\[[0-9;]*m", "", text)
        # 限制长度
        if len(text) > 10000:
            text = text[:10000]
        return text.strip()
