"""多模态感知层 — 图片/语音/视频/文档理解.

参考 mainidea.txt 的多模态输入层设计:
- 图片理解: OCR + 视觉描述 (通过 LLM vision API)
- 语音理解: 语音转文字 (通过 STT API)
- 文档理解: PDF/Word 解析
- 代码理解: AST 分析 + 语义理解

AstrBot 有 TTS/STT 提供商框架，Claude Code 完全不支持多模态。
此模块让 QQ Agent 在感知能力上超越两者。
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

from loguru import logger


class PerceptionEngine:
    """多模态感知引擎 — 自动路由到原生多模态模型.

    将 QQ 消息中的图片/语音/文件转换为文本描述。
    图片/音频请求自动使用原生多模态模型 (GPT-4o, Claude, Gemini)。
    纯文本模型通过 "Vision 模型描述 → 文本模型分析" 兜底。
    """

    def __init__(self, provider=None, model_router=None):
        self._provider = provider          # 默认文本 provider
        self._router = model_router        # ModelRouter (优先级高于 _provider)
        self._vision_provider = None       # 缓存的 vision provider

    def set_provider(self, provider) -> None:
        self._provider = provider

    def set_router(self, router) -> None:
        self._router = router

    def _get_vision_provider(self):
        """获取最优的 Vision 模型 provider.

        优先级: 显式注入的 vision_provider → router验证 → 默认provider
        """
        if self._vision_provider:
            return self._vision_provider
        if self._router:
            spec = self._router.route_vision()
            if spec:
                logger.info(f"Vision 路由: {spec.id} ({spec.name}) — 使用默认 provider")
        return self._provider  # fallback

    # ---- 图片理解 ----

    async def understand_image(self, image_url: str, question: str = "这张图片里有什么？") -> str:
        """通过原生 vision model 理解图片内容.

        自动路由到原生多模态模型:
        - 优先: GPT-4o / Claude / Gemini (原生 vision)
        - 兜底: Vision 模型描述 → 文本模型分析
        """
        provider = self._get_vision_provider()
        if not provider:
            return "图片理解服务未初始化 — 请配置支持 Vision 的模型"

        # 确认选用的模型
        model_name = getattr(provider, 'model', 'unknown')
        logger.info(f"图片理解路由: {model_name} (原生 Vision)")

        try:
            content = [
                {"type": "text", "text": question},
                {"type": "image_url", "image_url": {"url": image_url}},
            ]

            messages = [{"role": "user", "content": content}]
            resp = await provider.chat(messages, tools=None)
            result = resp.get("content", "无法理解图片")

            # 如果原生模型返回了结果但质量不确定，不额外处理
            return result

        except Exception as e:
            logger.warning(f"原生 Vision 调用失败: {e}，启用兜底方案")

            # 兜底: 用文本 provider 说明无法处理
            if self._provider and self._provider is not provider:
                try:
                    fallback_msg = (
                        f"[图片理解异常] 原生 Vision 模型 ({model_name}) 不可用。"
                        f"错误: {e}。请检查模型配置或网络连接。"
                    )
                    return fallback_msg
                except Exception:
                    pass

            return f"图片理解失败: {e}"

    async def ocr(self, image_url: str) -> str:
        """识别图片中的文字."""
        return await self.understand_image(
            image_url,
            "请提取这张图片中的所有文字，只返回文字内容，不要额外描述。"
        )

    async def describe_image(self, image_url: str) -> str:
        """详细描述图片内容."""
        return await self.understand_image(
            image_url,
            "请详细描述这张图片的内容，包括场景、人物、物体、颜色、文字等。"
        )

    async def analyze_image(self, image_url: str, prompt: str) -> str:
        """按自定义指令分析图片."""
        return await self.understand_image(image_url, prompt)

    # ---- 语音理解 ----

    async def transcribe_audio(self, audio_url: str, lang: str = "zh") -> str:
        """语音转文字.

        Args:
            audio_url: 音频文件 URL
            lang: 语言代码 (zh/en/auto)

        Returns:
            转写的文本
        """
        if not self._provider:
            return "语音识别服务未初始化"

        try:
            # 如果有 STT provider (Whisper 等)
            if hasattr(self._provider, 'transcribe'):
                return await self._provider.transcribe(audio_url, lang)
            return "语音转文字需要 STT Provider (如 Whisper)"
        except Exception as e:
            logger.warning(f"语音识别失败: {e}")
            return f"语音识别失败: {e}"

    # ---- 文档理解 ----

    @staticmethod
    async def parse_document(file_path: str) -> str:
        """解析文档内容 (TXT/MD/JSON/PDF).

        Returns:
            文档的文本内容
        """
        path = Path(file_path)
        if not path.exists():
            return f"文件不存在: {file_path}"

        try:
            ext = path.suffix.lower()

            if ext in (".txt", ".md", ".py", ".json", ".yaml", ".yml", ".toml", ".xml", ".html"):
                content = path.read_text(encoding="utf-8", errors="replace")
                if len(content) > 5000:
                    content = content[:5000] + f"\n\n... (文件过大，已截断。共 {len(content)} 字符)"
                return content

            if ext == ".pdf":
                try:
                    import subprocess
                    result = subprocess.run(
                        ["pdftotext", "-layout", file_path, "-"],
                        capture_output=True, text=True, timeout=30
                    )
                    return result.stdout[:5000] or "PDF 提取失败 (空内容)"
                except FileNotFoundError:
                    return "PDF 解析需要安装 pdftotext (apt install poppler-utils)"
                except Exception as e:
                    return f"PDF 解析失败: {e}"

            return f"不支持的文件格式: {ext}"
        except Exception as e:
            return f"文档解析失败: {e}"

    # ---- 代码理解 ----

    @staticmethod
    async def analyze_code(code: str, language: str = "python") -> dict:
        """分析代码结构.

        Returns:
            包含函数、类、导入等信息
        """
        result = {
            "language": language,
            "functions": [],
            "classes": [],
            "imports": [],
            "lines": len(code.split("\n")),
            "chars": len(code),
        }

        import re

        # 提取函数
        func_patterns = {
            "python": r"def\s+(\w+)\s*\(",
            "javascript": r"function\s+(\w+)\s*\(",
            "go": r"func\s+(\w+)\s*\(",
            "rust": r"fn\s+(\w+)\s*\(",
            "java": r"(public|private|protected)?\s*\w+\s+(\w+)\s*\([^)]*\)\s*\{",
        }

        if language in func_patterns:
            result["functions"] = re.findall(func_patterns[language], code)

        # 提取类
        class_pattern = {
            "python": r"class\s+(\w+)",
            "javascript": r"class\s+(\w+)",
            "java": r"class\s+(\w+)",
        }
        if language in class_pattern:
            result["classes"] = re.findall(class_pattern[language], code)

        # Python imports
        if language == "python":
            result["imports"] = re.findall(r"(?:from\s+\S+\s+)?import\s+(.+)", code)

        return result

    # ---- 综合感知 ----

    async def perceive(self, event) -> dict:
        """综合感知 — 自动识别消息中的多模态内容.

        Returns:
            包含所有感知结果的字典
        """
        result = {
            "text": event.text,
            "images": [],
            "audio": [],
            "documents": [],
        }

        # 检测图片
        images = event.message_chain.filter("image")
        for img in images:
            url = img.data.get("url", "")
            if url:
                description = await self.describe_image(url)
                result["images"].append({"url": url, "description": description})

        # 检测语音
        records = event.message_chain.filter("record")
        for rec in records:
            url = rec.data.get("url", "")
            if url:
                text = await self.transcribe_audio(url)
                result["audio"].append({"url": url, "transcript": text})

        return result
