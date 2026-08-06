"""外部插件加载回归:验证 data/plugins 下所有插件可加载且元数据合法。

- 每个 .py 插件能通过 PluginRegistry.load_from_directory 加载
- 每个 .py 有同目录同名 .json 元数据
- 元数据包含 name/version/author/description/commands/dependencies 字段
- 记录 handler 类型分布,防止意外回归

运行:python -m src.tests.plugin_load_test
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.plugins.registry import PluginRegistry

PLUGIN_DIR = ROOT / "src" / "data" / "plugins"
REQUIRED_FIELDS = ("name", "version", "author", "description", "commands", "dependencies")
REQUIRED_TYPES = ("json", "py")

failures: list[str] = []


async def check() -> None:
    py_files = sorted(PLUGIN_DIR.glob("*.py"))
    json_files = sorted(PLUGIN_DIR.glob("*.json"))
    py_stems = {p.stem for p in py_files}
    json_stems = {p.stem for p in json_files}

    # 1) 一一对应:每个 .py 有 .json,每个 .json 有 .py
    for stem in py_stems - json_stems:
        failures.append(f"缺少元数据: {stem}.py 没有对应 {stem}.json")
    for stem in json_stems - py_stems:
        failures.append(f"多余元数据: {stem}.json 没有对应 {stem}.py")

    # 2) 元数据字段完整性
    for jf in json_files:
        try:
            meta = json.loads(jf.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            failures.append(f"元数据 JSON 非法: {jf.name} ({exc})")
            continue
        if not isinstance(meta, dict):
            failures.append(f"元数据非对象: {jf.name}")
            continue
        for field in REQUIRED_FIELDS:
            if field not in meta:
                failures.append(f"元数据缺少字段 {field}: {jf.name}")

    # 3) 全量加载,收集加载失败
    registry = PluginRegistry()
    loaded = await registry.load_from_directory(PLUGIN_DIR)
    missing = py_stems - set(loaded)
    for stem in sorted(missing):
        failures.append(f"加载失败: {stem}.py")

    # 4) handler 分布统计
    types: dict[str, int] = {}
    for h in registry._handlers:
        types[h.matcher_type] = types.get(h.matcher_type, 0) + 1
    print(f"插件: {len(loaded)}/{len(py_files)} 个 | handler: {registry.handler_count()} 个")
    print(f"handler 分布: {types}")
    print(f"元数据: {len(json_files)} 个 JSON")


async def main() -> None:
    if not PLUGIN_DIR.is_dir():
        print(f"SKIP: 目录不存在 {PLUGIN_DIR}")
        return
    await check()
    if failures:
        print("\n".join(f"FAIL: {f}" for f in failures))
        sys.exit(1)
    print("PASS: 全部插件加载与元数据校验通过")


if __name__ == "__main__":
    asyncio.run(main())
