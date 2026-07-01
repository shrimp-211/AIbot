"""Vision 工具 — 图片/OCR/场景分析 (调用原生Vision模型)."""

from typing import Any
from .base import BaseTool


class VisionTool(BaseTool):
    name = "vision"
    description = "分析图片内容：识别物体、场景、文字(OCR)、图表等。使用原生Vision模型(GPT-4o/Claude/Gemini)。"
    permission_level = 0
    perception = None

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {
            "image_url": {"type": "string", "description": "图片URL"},
            "task": {"type": "string", "enum": ["describe", "ocr", "analyze"], "description": "describe=描述图片, ocr=提取文字, analyze=按提示分析"},
            "prompt": {"type": "string", "description": "自定义分析提示 (仅analyze时有效)"},
        }, "required": ["image_url", "task"]}

    async def execute(self, image_url: str, task: str = "describe", prompt: str = "", **kwargs) -> str:
        if not self.perception:
            return "图片分析未初始化 — 请配置Vision模型"
        if task == "describe":
            return await self.perception.describe_image(image_url)
        if task == "ocr":
            return await self.perception.ocr(image_url)
        if task == "analyze":
            return await self.perception.analyze_image(image_url, prompt or "请详细分析这张图片")
        return f"未知task: {task}"
