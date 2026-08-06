"""插件元数据规范:解析与校验 metadata.yaml(兼容 plugin.json)。

目录规范(参照 nonebot/astrbot 插件目录):
    my_plugin/
    ├── metadata.yaml        # 元数据(必需字段: name, entry)
    ├── __init__.py 或 main.py  # 插件入口(setup(registry) / register(registry))
    └── requirements.txt      # 可选,安装时自动 pip install

metadata.yaml 字段:
    name / version / description / author / homepage / license
    dependencies: [pkg...]     # pip 依赖
    commands: {cmd: 描述}       # 供 WebUI 展示
    entry: __init__.py          # 入口文件名(默认 __init__.py)
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

_REQUIRED = ("name",)

_SECTION_MAP = {
    "version": "version",
    "description": "description",
    "author": "author",
    "homepage": "homepage",
    "license": "license",
    "dependencies": "dependencies",
    "commands": "commands",
    "entry": "entry",
}


def parse_metadata(plugin_dir: str | Path) -> dict[str, Any]:
    """读取插件目录的 metadata.yaml / plugin.json,返回规范化元数据(失败抛 ValueError)。"""
    d = Path(plugin_dir)
    meta: dict[str, Any] = {}

    yaml_path = d / "metadata.yaml"
    json_path = d / "plugin.json"
    if yaml_path.is_file():
        try:
            raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            raise ValueError(f"metadata.yaml 解析失败: {exc}") from exc
        if not isinstance(raw, dict):
            raise ValueError("metadata.yaml 根节点必须是映射")
        meta.update(raw)
    elif json_path.is_file():
        try:
            raw = json.loads(json_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            raise ValueError(f"plugin.json 解析失败: {exc}") from exc
        if not isinstance(raw, dict):
            raise ValueError("plugin.json 根节点必须是映射")
        meta.update(raw)

    if not meta:
        raise ValueError("插件目录缺少 metadata.yaml 或 plugin.json")
    missing = [k for k in _REQUIRED if not meta.get(k)]
    if missing:
        raise ValueError(f"插件元数据缺少必填字段: {', '.join(missing)}")
    meta.setdefault("version", "0.0.0")
    meta.setdefault("description", "")
    meta.setdefault("dependencies", [])
    meta.setdefault("commands", {})
    meta.setdefault("entry", "__init__.py")
    return meta


def entry_file(plugin_dir: str | Path) -> Path | None:
    """返回插件入口文件(entry 字段指定的 .py,不存在则回退常见入口)。"""
    d = Path(plugin_dir)
    try:
        entry = parse_metadata(d).get("entry", "__init__.py")
    except ValueError:
        entry = "__init__.py"
    cands = [d / str(entry), d / "main.py", d / "__init__.py"]
    for c in cands:
        if c.is_file():
            return c
    return None
