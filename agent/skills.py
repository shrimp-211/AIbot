"""技能(Skills)系统:SKILL.md 加载、解析与注册(参考 Claude Code 的 SKILL.md 格式)。

技能文件为 Markdown,YAML frontmatter 声明元数据,正文为技能指令:

    ---
    name: web-researcher
    description: 深度网络调研
    tools: [web_search, web_fetch]   # 可选:工具白名单(缺省=全部)
    plan_only: false                 # 可选:true 表示只读技能
    ---
    # 深度调研
    使用 web_search 检索...,使用 web_fetch 抓取...
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None


_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


@dataclass
class Skill:
    name: str
    description: str = ""
    content: str = ""
    tools: list[str] | None = None  # None=全部工具, []=无工具, [...]=白名单
    plan_only: bool = False
    triggers: list[str] | None = None  # 触发词(仅用于自动匹配)
    path: str | None = None

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "tools": self.tools,
            "plan_only": self.plan_only,
            "triggers": self.triggers,
            "path": self.path,
        }


def parse_skill_file(path: Path) -> Skill | None:
    """解析单个 SKILL.md 文件,失败返回 None。"""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None

    frontmatter: dict[str, Any] = {}
    body = text
    m = _FRONTMATTER_RE.match(text)
    if m:
        try:
            if yaml is not None:
                frontmatter = yaml.safe_load(m.group(1)) or {}
            else:  # pragma: no cover
                frontmatter = _minimal_yaml(m.group(1))
        except Exception:  # noqa: BLE001
            logger.warning(f"技能 frontmatter 解析失败: {path}")
        body = text[m.end() :].strip()

    name = str(frontmatter.get("name") or path.stem).strip()
    if not name:
        return None
    return Skill(
        name=name,
        description=str(frontmatter.get("description", "")).strip(),
        content=body,
        tools=frontmatter.get("tools"),
        plan_only=bool(frontmatter.get("plan_only", False)),
        triggers=frontmatter.get("triggers"),
        path=str(path),
    )


def _minimal_yaml(text: str) -> dict:
    """无 pyyaml 时的兜底解析(name/description/tools 简单字段)。"""
    result: dict[str, Any] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" in line:
            k, v = line.split(":", 1)
            k, v = k.strip(), v.strip()
            if v.startswith("[") and v.endswith("]"):
                result[k] = [x.strip().strip("'\"") for x in v[1:-1].split(",") if x.strip()]
            else:
                result[k] = v.strip("'\"")
    return result


class SkillRegistry:
    """技能注册中心:扫描目录、查找、会话级激活。"""

    def __init__(self, extra_dirs: list[str] | None = None):
        self._skills: dict[str, Skill] = {}
        self._active: dict[str, str] = {}  # session_id -> skill_name
        self._extra_dirs = extra_dirs or []

    def load_directory(self, path: str | Path) -> int:
        """扫描目录下所有 .md 技能文件,返回加载数量。"""
        p = Path(path)
        if not p.is_dir():
            return 0
        count = 0
        for f in sorted(p.glob("*.md")):
            skill = parse_skill_file(f)
            if skill:
                self._skills[skill.name] = skill
                count += 1
        return count

    def reload(self, builtin_dir: str, data_dir: str | None = None) -> int:
        """重新加载全部技能目录。"""
        self._skills.clear()
        count = self.load_directory(builtin_dir)
        if data_dir:
            count += self.load_directory(data_dir)
        return count

    # ---------- 查找 ----------

    def get(self, name: str) -> Skill | None:
        return self._skills.get(name)

    def all(self) -> list[Skill]:
        return list(self._skills.values())

    def names(self) -> list[str]:
        return list(self._skills.keys())

    # ---------- 会话级激活 ----------

    def activate(self, session_id: str, name: str) -> dict:
        skill = self._skills.get(name)
        if skill is None:
            return {"error": f"技能不存在: {name}"}
        self._active[session_id] = name
        return {"ok": True, "skill": name, "description": skill.description}

    def deactivate(self, session_id: str) -> dict:
        was = self._active.pop(session_id, None)
        return {"ok": True, "skill": was} if was else {"ok": True, "message": "当前无激活技能"}

    def active(self, session_id: str) -> Skill | None:
        name = self._active.get(session_id)
        return self._skills.get(name) if name else None

    # ---------- 文本自动匹配 ----------

    def auto_select(self, text: str) -> Skill | None:
        """根据名称/描述/触发词自动匹配技能(简单打分,阈值 2)。"""
        text_l = text.lower()
        best, best_score = None, 0
        for skill in self._skills.values():
            score = 0
            if skill.name and skill.name.lower() in text_l:
                score += 3
            if skill.description:
                for kw in _split_keywords(skill.description):
                    if kw and kw in text:
                        score += 1
            if skill.triggers:
                for trig in skill.triggers:
                    if trig and trig in text:
                        score += 2
            if score > best_score:
                best, best_score = skill, score
        return best if best_score >= 2 else None

    def tool_filter(self, session_id: str) -> list[str] | None:
        """返回当前激活技能的只读工具白名单(None=不限制)。"""
        skill = self.active(session_id)
        if skill is None:
            return None
        if skill.tools is not None and skill.tools == []:
            return []
        return skill.tools


def _split_keywords(text: str) -> list[str]:
    """从描述中提取关键词:中文整词 + 2 字滑动窗口;英文 3+ 字母词。"""
    words: list[str] = []
    for w in re.split(r"[\s,，。;；:：]+", text):
        w = w.strip()
        if not w:
            continue
        if "一" <= w[0] <= "鿿":
            words.append(w)
            if len(w) >= 3:
                words.extend(w[i : i + 2] for i in range(len(w) - 1))
        elif len(w) >= 3:
            words.append(w)
    return list(dict.fromkeys(words))
