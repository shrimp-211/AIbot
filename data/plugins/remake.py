from __future__ import annotations
import random
from src.adapter.event import AgentEvent

_TALENTS = ["天选之人","富二代","学霸","运动健将","艺术天才","商业奇才","社畜命","普通人","幸运儿","倒霉蛋","穿越者"]
_LIFE = [
    ("0-5岁", [(0,15,"出生时哭得格外响亮"),(16,30,"健康快乐地成长"),(31,40,"天资聪颖,三岁能诵诗")]),
    ("6-12岁", [(0,12,"成绩垫底"),(13,25,"成绩中等偏上"),(26,40,"年年第一,奖状贴满墙壁")]),
    ("13-17岁", [(0,10,"辍学搬砖"),(11,22,"读完中学"),(23,32,"考上省重点"),(33,40,"保送顶尖学府")]),
    ("18-24岁", [(0,10,"工厂流水线"),(11,22,"普通大学"),(23,32,"考入985"),(33,40,"创业获千万融资")]),
    ("25-34岁", [(0,10,"失业啃老"),(11,22,"普通工作"),(23,32,"升主管年薪百万"),(33,40,"公司上市财务自由")]),
    ("35-49岁", [(0,10,"中年危机"),(11,25,"家庭和睦"),(26,35,"事业再攀高峰"),(36,40,"功成名就著书立说")]),
    ("50-64岁", [(0,15,"缠绵病榻"),(16,28,"光荣退休"),(29,40,"游山玩水")]),
    ("65-79岁", [(0,18,"因病离世"),(19,30,"安度晚年"),(31,40,"广场舞领舞")]),
    ("80岁+", [(0,25,"已入极乐"),(26,35,"百岁老人五世同堂"),(36,40,"创造长寿纪录")]),
]

def setup(registry) -> None:
    @registry.command("remake", permission_level=0)
    async def handler(event: AgentEvent):
        looks = random.randint(1,10); smart = random.randint(1,10)
        body = random.randint(1,10); wealth = random.randint(1,10)
        total = looks+smart+body+wealth
        talent = random.choice(_TALENTS)
        lines = [
            "=== 人生重开 ===",
            f"天赋: {talent}",
            f"颜值:{looks} 智力:{smart} 体质:{body} 家境:{wealth} 总评:{total}/40",
            "","=== 人生历程 ===",
        ]
        alive = True; age = 0
        for stage_name, events in _LIFE:
            if not alive: break
            ev = "平静度过"
            for lo,hi,t in events:
                if lo<=total<=hi: ev=t; break
            lines.append(f"{stage_name}: {ev}")
            age+=random.randint(3,20)
            if "死" in ev or "极乐" in ev: alive=False
            elif stage_name!="80岁+" and random.randint(1,10)>max(1,body-2):
                lines.append(f"  体质太差,{age}岁英年早逝..."); alive=False
        if alive: lines.append(f"活到{age}岁,人生圆满!")
        else: lines.append(f"享年{age}岁。重新来过!")
        await event.reply("\n".join(lines))
        return None
