"""响应阶段:逐段发送回复,段间间隔防刷屏。"""
from __future__ import annotations

import asyncio

from ...adapter.event import AgentEvent
from ...adapter.message import escape_cq
from ..scheduler import Stage


class RespondStage(Stage):
    def __init__(self, interval: float = 0.3, hooks=None):
        self._interval = interval
        self._hooks = hooks

    @staticmethod
    def _segment_reply(text: str, max_len: int = 60, max_segments: int = 8) -> list[str]:
        """智能切块(QQ 流式体验):按换行/句边界切,合并短块,不切断 CQ 码,上限段数。

        - 模型输出经提示词引导自然分点/分段(参照"AI 自行分段")
        - 此处做兜底切块:优先段落边界,再句边界,避免生硬逐字切
        - `[CQ:...]` 码行独立成段,不被截断
        """
        import re

        text = (text or "").strip()
        if not text:
            return []
        if len(text) <= max_len:
            return [text]
        lines = [l.strip() for l in text.split("\n") if l.strip()]
        if not lines:
            return [text]
        segments: list[str] = []
        buf = ""
        for line in lines:
            if line.startswith("[CQ:"):
                if buf:
                    segments.append(buf)
                    buf = ""
                segments.append(line)
                continue
            if len(buf) + len(line) <= max_len:
                buf = (buf + line).strip()
            else:
                if buf:
                    segments.append(buf)
                if len(line) > max_len:
                    cur = ""
                    for s in re.split(r"(?<=[。！？!?；;])", line):
                        if cur and len(cur) + len(s) > max_len:
                            segments.append(cur)
                            cur = ""
                        cur += s
                    buf = cur
                else:
                    buf = line
        if buf:
            segments.append(buf)
        # 合并过短段 + 段数上限
        merged: list[str] = []
        for s in segments:
            if merged and len(merged[-1]) + len(s) <= max_len and len(merged) < max_segments:
                merged[-1] += s
            else:
                merged.append(s)
        if len(merged) > max_segments:
            merged = merged[: max_segments - 1] + [" ".join(merged[max_segments - 1 :])]
        return merged

    async def process(self, event: AgentEvent) -> None:
        segments = event.state.get("reply_segments") or []
        if not segments and event.state.get("reply"):
            segments = [str(event.state["reply"])]
        if not segments:
            return
        # QQ 长回复智能分段:模拟流式体验(WebUI 已走真流式,不重复分段)
        out_segments: list[str] = []
        for seg in segments:
            if event.platform == "qq" and len(seg) > 40:
                out_segments.extend(self._segment_reply(seg))
            else:
                out_segments.append(seg)
        # Agent 最终回复是 LLM 生成的不可信文本,必须转义 CQ 码防注入
        # (插件/工具直连 event.reply 的路径由调用方自行转义,这里不动)
        for i, seg in enumerate(out_segments):
            await event.reply(escape_cq(seg), at=(i == 0 and event.is_tome))
            if self._hooks is not None:
                try:
                    await self._hooks.trigger("after_message_sent", text=seg, event=event)
                except Exception:  # noqa: BLE001
                    pass
            if i < len(out_segments) - 1 and self._interval:
                await asyncio.sleep(self._interval)
