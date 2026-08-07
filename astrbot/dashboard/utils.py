"""Dashboard 工具函数(本项目适配:t-SNE 可视化降级为 None)。"""

from __future__ import annotations


async def generate_tsne_visualization(
    query: str,
    kb_names: list[str],
    kb_manager,
) -> str | None:
    """生成 t-SNE 可视化(本项目降级:返回 None,不依赖 matplotlib/faiss 可选库)。"""
    return None
