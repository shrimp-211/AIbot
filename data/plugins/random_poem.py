"""诗词插件:随机推送一句古诗词。

在线接口:今日诗词 API(v1.jinrishici.com),无需 Key;失败时用本地诗词库兜底。
命令:
- /诗词      随机一句诗词
- /诗词更多   多给几句
"""
from __future__ import annotations

import random

import aiohttp

from src.adapter.event import AgentEvent
from src.adapter.message import escape_cq

_API = "https://v1.jinrishici.com/one.json"
_LOCAL_POEMS = [
    ("床前明月光,疑是地上霜。", "李白《静夜思》"),
    ("人生得意须尽欢,莫使金樽空对月。", "李白《将进酒》"),
    ("会当凌绝顶,一览众山小。", "杜甫《望岳》"),
    ("海上生明月,天涯共此时。", "张九龄《望月怀远》"),
    ("此情可待成追忆,只是当时已惘然。", "李商隐《锦瑟》"),
    ("大江东去,浪淘尽,千古风流人物。", "苏轼《念奴娇·赤壁怀古》"),
    ("问渠那得清如许?为有源头活水来。", "朱熹《观书有感》"),
    ("长风破浪会有时,直挂云帆济沧海。", "李白《行路难》"),
    ("春风得意马蹄疾,一日看尽长安花。", "孟郊《登科后》"),
    ("落霞与孤鹜齐飞,秋水共长天一色。", "王勃《滕王阁序》"),
]


def _local() -> str:
    line, src = random.choice(_LOCAL_POEMS)
    return f"{line}\n—— {src}"


async def _fetch() -> str | None:
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as _s:
            async with _s.get(_API) as resp:
                data = await resp.json(content_type=None)
        if data.get("status") == "success" and data.get("content"):
            origin = data.get("origin", "")
            author = data.get("author", "")
            source = f"{origin} · {author}" if origin and author else origin or author
            return f"{data['content']}\n—— {source}"
    except Exception:  # noqa: BLE001
        return None
    return None


def setup(registry) -> None:
    @registry.command("诗词", permission_level=1)
    async def poem(event: AgentEvent):
        text = await _fetch() or _local()
        await event.reply(f"📜 {escape_cq(text)}")
        return None

    @registry.command("诗词更多", permission_level=1)
    async def poem_more(event: AgentEvent):
        lines = [escape_cq(_local()) for _ in range(3)]
        await event.reply("📜 随机三句:\n" + "\n\n".join(lines))
        return None
