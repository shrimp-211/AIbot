"""成语接龙插件:多轮会话游戏(本地成语库,无网络依赖)。

规则:轮流说成语,下一个成语首字必须与上一个末字相同(仅判同字,同音不算)。
机器人本地接龙,接不上时玩家获胜。
命令:
- /成语接龙      开始新一局
- 游戏中直接发成语继续;发「认输」「结束」「退出」结束
"""
from __future__ import annotations

import random

from src.adapter.event import AgentEvent
from src.adapter.message import escape_cq

_IDIOMS = (
    "一心一意", "意犹未尽", "尽力而为", "为人师表", "表里如一", "一鸣惊人",
    "人心所向", "向善若水", "水滴石穿", "穿针引线", "见缝插针", "针锋相对",
    "对症下药", "药到病除", "除暴安良", "良辰美景", "锦上添花", "花好月圆",
    "源远流长", "长驱直入", "入木三分", "分秒必争", "争分夺秒", "妙不可言",
    "言而有信", "信誓旦旦", "旦夕祸福", "福星高照", "照猫画虎", "虎背熊腰",
    "腰缠万贯", "贯朽粟陈", "陈词滥调", "调兵遣将", "将心比心", "心平气和",
    "和风细雨", "雨过天晴", "晴空万里", "里应外合", "合二为一", "一诺千金",
    "金玉满堂", "堂而皇之", "之乎者也", "夜长梦多", "多才多艺",
    "艺高人胆大", "大惊小怪", "怪模怪样", "样样俱全", "全力以赴", "赴汤蹈火",
    "火上浇油", "油嘴滑舌", "舌战群儒", "如虎添翼", "翼翼小心", "心想事成",
    "成竹在胸", "胸有成竹", "竹报平安", "安居乐业", "业精于勤", "勤能补拙",
    "拙口笨舌", "舌粲莲花", "花团锦簇", "簇拥而至", "至高无上", "上行下效",
    "效犬马力", "力挽狂澜", "澜倒波随", "随机应变", "变化莫测", "测字先生",
    "生生不息", "息事宁人", "人杰地灵", "灵机一动", "动人心弦", "弦外之音",
    "音容笑貌", "貌合神离", "离群索居", "居安思危", "危言耸听", "听天由命",
    "命中注定", "定国安邦", "邦家之光", "光明磊落", "落井下石", "石沉大海",
    "海阔天空", "空谷幽兰", "兰质蕙心", "心驰神往", "继往开来", "来日方长",
    "长命百岁", "岁寒三友", "有目共睹", "睹物思人", "人山人海", "海纳百川",
    "川流不息", "息息相关", "关门大吉", "吉人天相", "相辅相成", "成人之美",
)

_STOP_WORDS = ("认输", "结束", "退出", "不玩了", "算了")

_TTL = 600  # 单回合超时(秒)


def _build_index() -> dict[str, list[str]]:
    idx: dict[str, list[str]] = {}
    for w in _IDIOMS:
        idx.setdefault(w[0], []).append(w)
    return idx


_IDX = _build_index()


def _pick_start() -> str:
    """选一个末字有接续的成语作为开场,避免开局即死。"""
    pool = [w for w in _IDIOMS if w[-1] in _IDX]
    return random.choice(pool) if pool else random.choice(_IDIOMS)


def _bot_reply(last_char: str, exclude: set[str]) -> str | None:
    candidates = [w for w in _IDX.get(last_char, []) if w not in exclude]
    return random.choice(candidates) if candidates else None


def setup(registry) -> None:
    def _pending(sid: str, data: dict) -> None:
        registry.sessions.schedule(sid, _turn, "word", data, ttl=_TTL)

    async def _turn(event: AgentEvent, data: dict) -> None:
        word = (data.get("word") or "").strip()
        if word in _STOP_WORDS or word.startswith("/"):
            await event.reply("游戏结束,下次再来~")
            return None
        last = data.get("last", "")
        if word not in _IDIOMS:
            await event.reply(f"「{escape_cq(word)}」不在成语库中,再来一个试试?(要接「{last[-1]}」开头)")
            _pending(event.session_id, data)
            return None
        if word[0] != last[-1]:
            await event.reply(f"接错啦!要接「{last[-1]}」开头,不是「{word[0]}」")
            _pending(event.session_id, data)
            return None
        rounds = data.get("rounds", 0) + 1
        reply = _bot_reply(word[-1], {word})
        if reply is None:
            await event.reply(
                f"【{word}】✓ 我接不上啦,你赢了!🎉 共 {rounds} 回合"
            )
            return None
        data["last"] = reply
        data["rounds"] = rounds
        await event.reply(
            f"【{word}】✓ 我接:【{reply}】\n请接「{reply[-1]}」开头"
        )
        _pending(event.session_id, data)
        return None

    @registry.command("成语接龙")
    async def start(event: AgentEvent):
        opening = _pick_start()
        await event.reply(
            f"🐉 成语接龙开始!我先来:【{opening}】\n"
            f"请接「{opening[-1]}」开头的成语(发「认输」结束)"
        )
        _pending(event.session_id, {"last": opening, "rounds": 0, "word": None})
        return None
