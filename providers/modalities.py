"""Provider 能力声明(模态):按能力过滤工具与上下文,避免向不支持模态的模型传多余内容。

参照 AstrBot 的 modalities.py(provider 声明能力 → 上层按能力裁剪)。
"""
from __future__ import annotations

# 模态常量
MODALITY_TEXT = "text"        # 纯文本
MODALITY_IMAGE = "image"      # 图片输入
MODALITY_AUDIO = "audio"      # 音频输入
MODALITY_VIDEO = "video"      # 视频输入
MODALITY_STREAMING = "streaming"  # 支持流式输出
MODALITY_FUNCTION_CALL = "function_call"  # 支持函数调用(工具)

ALL_MODALITIES = frozenset(
    {
        MODALITY_TEXT,
        MODALITY_IMAGE,
        MODALITY_AUDIO,
        MODALITY_VIDEO,
        MODALITY_STREAMING,
        MODALITY_FUNCTION_CALL,
    }
)

# 默认文本模型具备的能力(最少集合)
DEFAULT_MODALITIES = frozenset({MODALITY_TEXT, MODALITY_FUNCTION_CALL})


def supports(modalities: frozenset[str] | set[str] | None, modality: str) -> bool:
    """判断能力集合是否支持指定模态(空集合视为只支持默认)。"""
    if not modalities:
        return modality in DEFAULT_MODALITIES
    return modality in modalities


def filter_tools_for_modalities(
    schemas: list[dict], modalities: frozenset[str] | set[str] | None
) -> list[dict]:
    """按模态过滤工具 schema 列表。

    工具可在 schema 顶层声明 `x_modal: "image"` 等(由 Tool.requires_modal 输出),
    表示该工具依赖特定模态能力(如 vision_analyze 需要 image 输入)。模型不支持时剔除。
    未声明模态的工具视为文本工具,总是保留。
    """
    if not modalities or supports(modalities, MODALITY_IMAGE):
        # 支持图像即视为多模态,保留全部
        return schemas
    return [
        s for s in schemas if not s.get("x_modal") or s.get("x_modal") not in ("image",)
    ]
