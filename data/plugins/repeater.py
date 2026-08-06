"""复读机插件:群内同一非命令消息连续出现 3 次时,第 4 次复读。

参考 nonebot-plugin-repeater。基于新管道 PluginStage,插件可见全部消息
(无需 @ 唤醒),因此能统计连续消息。状态持久化到 JsonKV。
"""
from __future__ import annotations

from src.adapter.event import AgentEvent
from src.adapter.message import escape_cq
from src.storage.db import JsonKV

_KEY = "plugin:repeater"


def setup(registry) -> None:
    @registry.message(priority=200, block=False)
    async def repeater(event: AgentEvent, db: JsonKV):
        if event.message_type != "group" or not event.group_id:
            return None
        if event.is_tome:
            return None
        text = event.plain_text.strip()
        if not text or text.startswith(("/", "!")):
            return None  # 命令不复读
        store = db.get(_KEY) or {}
        cur = store.get(event.group_id) or {"count": 0, "text": ""}
        if cur.get("text") != text:
            # 新消息:重置计数并写盘
            cur = {"count": 1, "text": text}
            store[event.group_id] = cur
            db.set(_KEY, store)
            return None
        cur["count"] += 1
        if cur["count"] < 3:
            # 第 2 次相同:不写盘,降低写入放大(崩溃仅回退一次计数)
            return None
        cur["count"] = 0  # 复读后归零,避免持续刷屏
        store[event.group_id] = cur
        db.set(_KEY, store)
        await event.reply(escape_cq(text))
        return True
