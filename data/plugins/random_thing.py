"""随机推荐插件:解决选择困难症(纯本地硬编码,无网络依赖)。

命令:
- /吃什么 [数量]   今日美食推荐(数量 1-5,默认随机 1-3)
- /看什么          影视推荐
- /读什么          书籍推荐
- /掷骰子选 A 或 B  通用决策:随机选一个
"""
from __future__ import annotations

import random

from src.adapter.event import AgentEvent
from src.adapter.message import escape_cq

_FOODS = [
    ("红烧肉", "肥而不腻,入口即化"),
    ("麻婆豆腐", "麻辣鲜香,下饭神器"),
    ("宫保鸡丁", "酸甜微辣,鸡丁滑嫩"),
    ("鱼香肉丝", "咸甜酸辣,回味无穷"),
    ("糖醋里脊", "外酥里嫩,酸甜开胃"),
    ("水煮鱼", "麻辣过瘾,鱼片鲜嫩"),
    ("辣子鸡", "香辣酥脆,越吃越香"),
    ("酸菜鱼", "酸辣开胃,鱼肉细嫩"),
    ("回锅肉", "咸鲜微辣,肥瘦相宜"),
    ("西红柿炒蛋", "家常暖心,酸甜适中"),
    ("干锅花菜", "干香微辣,爽脆下饭"),
    ("清蒸鲈鱼", "清淡鲜美,原汁原味"),
    ("白切鸡", "皮爽肉滑,蘸料一绝"),
    ("东坡肉", "软糯香醇,入口即化"),
    ("北京烤鸭", "皮脆肉嫩,卷饼一绝"),
    ("兰州拉面", "汤清面劲,牛肉飘香"),
    ("重庆小面", "麻辣鲜香,过瘾上头"),
    ("陕西凉皮", "酸辣爽滑,夏日必吃"),
    ("小笼包", "皮薄馅大,汤汁鲜美"),
    ("虾饺", "晶莹剔透,虾肉弹牙"),
    ("生煎包", "底脆汁多,满口留香"),
    ("煎饼果子", "香脆可口,早餐首选"),
    ("牛肉火锅", "涮出鲜嫩,汤底浓郁"),
    ("麻辣香锅", "麻辣过瘾,食材丰富"),
    ("烧烤拼盘", "烟火气十足,越夜越香"),
    ("蛋炒饭", "粒粒分明,简单美味"),
    ("皮蛋瘦肉粥", "绵滑暖胃,咸香适口"),
    ("章鱼小丸子", "外脆内软,章鱼Q弹"),
    ("炸鸡", "外酥里嫩,快乐源泉"),
    ("提拉米苏", "咖啡香浓,入口丝滑"),
    ("珍珠奶茶", "奶香浓郁,珍珠弹牙"),
    ("杨枝甘露", "芒果香甜,清爽解腻"),
]

_MOVIES = [
    ("肖申克的救赎", "剧情"),
    ("教父", "犯罪"),
    ("霸王别姬", "剧情"),
    ("阿甘正传", "剧情"),
    ("千与千寻", "动画"),
    ("星际穿越", "科幻"),
    ("盗梦空间", "科幻"),
    ("泰坦尼克号", "爱情"),
    ("这个杀手不太冷", "剧情"),
    ("活着", "剧情"),
    ("天空之城", "动画"),
    ("楚门的世界", "剧情"),
    ("三傻大闹宝莱坞", "喜剧"),
    ("疯狂动物城", "动画"),
    ("摔跤吧!爸爸", "剧情"),
    ("我不是药神", "剧情"),
    ("流浪地球", "科幻"),
    ("让子弹飞", "喜剧"),
    ("无间道", "犯罪"),
    ("寻梦环游记", "动画"),
    ("请回答1988", "剧集"),
    ("漫长的季节", "剧集"),
]

_BOOKS = [
    ("三体", "刘慈欣"),
    ("活着", "余华"),
    ("百年孤独", "马尔克斯"),
    ("平凡的世界", "路遥"),
    ("白夜行", "东野圭吾"),
    ("小王子", "圣埃克苏佩里"),
    ("围城", "钱钟书"),
    ("红楼梦", "曹雪芹"),
    ("1984", "乔治·奥威尔"),
    ("人类简史", "尤瓦尔·赫拉利"),
    ("朝花夕拾", "鲁迅"),
    ("呐喊", "鲁迅"),
    ("老人与海", "海明威"),
    ("月亮与六便士", "毛姆"),
    ("道德经", "老子"),
    ("苏东坡传", "林语堂"),
]


def _parse_count(arg: str) -> int | None:
    """解析数量参数,非法返回 None。"""
    if not arg:
        return None
    try:
        n = int(arg)
    except ValueError:
        return None
    if 1 <= n <= 5:
        return n
    return None


def setup(registry) -> None:
    @registry.command("吃什么")
    async def eat(event: AgentEvent):
        arg = (event.state.get("command_arg") or "").strip()
        if arg:
            n = _parse_count(arg)
            if n is None:
                await event.reply("数量需在 1-5 之间。用法:/吃什么 [数量]")
                return None
        else:
            n = random.randint(1, 3)
        picks = random.sample(_FOODS, n)
        lines = [f"🍜 {escape_cq(name)} —— {escape_cq(desc)}" for name, desc in picks]
        await event.reply("今日推荐:\n" + "\n".join(lines))
        return None

    @registry.command("看什么")
    async def watch(event: AgentEvent):
        title, genre = random.choice(_MOVIES)
        await event.reply(f"🎬 推荐你看:《{escape_cq(title)}》\n类型:{escape_cq(genre)}")
        return None

    @registry.command("读什么")
    async def read(event: AgentEvent):
        title, author = random.choice(_BOOKS)
        await event.reply(f"📚 推荐你读:《{escape_cq(title)}》\n作者:{escape_cq(author)}")
        return None

    @registry.command("掷骰子选")
    async def choose(event: AgentEvent):
        arg = (event.state.get("command_arg") or "").strip()
        options = [s.strip() for s in arg.split("或") if s.strip()]
        if len(options) < 2:
            await event.reply("用法: /掷骰子选 A 或 B 或 C")
            return None
        if len(options) > 10:
            await event.reply("选项最多 10 个。")
            return None
        for opt in options:
            if len(opt) > 20:
                await event.reply("每个选项最多 20 字。")
                return None
        await event.reply(f"🎲 我选:{escape_cq(random.choice(options))}")
        return None
