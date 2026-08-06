"""Whisper 语音转文字(STT)。支持 OpenAI API 与本地 faster-whisper 两种后端。

- OpenAI 后端: config {type: whisper, api_key, model: whisper-1, base_url}
- 本地后端:  config {type: faster-whisper, model: small|base|...}
本地后端依赖 faster-whisper,未安装时自动回退 API;两者都不可用则抛错。
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from loguru import logger

from ..base import STTProvider


class WhisperSTTProvider(STTProvider):
    name = "whisper"

    def __init__(self, config: dict[str, Any]):
        super().__init__(config)
        self.model = config.get("model", "whisper-1")
        self.backend = config.get("backend", "api")  # api | local

    async def transcribe(self, audio_path: str, **kwargs: Any) -> str:
        path = Path(audio_path)
        if not path.is_file():
            raise FileNotFoundError(f"音频文件不存在: {audio_path}")
        # 本地 faster-whisper 优先(若显式选择或 API 未配置)
        if self.backend == "local":
            try:
                return await self._transcribe_local(path)
            except ImportError:
                logger.warning("faster-whisper 未安装,回退 OpenAI Whisper API")
            except Exception as exc:  # noqa: BLE001
                logger.warning("本地转写失败,回退 API: {}", exc)
        return await self._transcribe_api(path)

    async def _transcribe_local(self, path: Path) -> str:
        # 惰性导入,避免未安装 faster-whisper 时阻塞启动
        from faster_whisper import WhisperModel

        def _run() -> str:
            model = WhisperModel(self.model, device="cpu", compute_type="int8")
            segments, _ = model.transcribe(str(path), language=None)
            return "".join(s.text for s in segments).strip()

        return await asyncio.to_thread(_run)

    async def _transcribe_api(self, path: Path) -> str:
        api_key = self.config.get("api_key", "")
        if not api_key:
            raise RuntimeError("Whisper API 未配置 api_key(可在 config.yaml 的 provider_stt 段设置)")
        import httpx

        base_url = (self.config.get("base_url") or "https://api.openai.com/v1").rstrip("/")
        url = f"{base_url}/audio/transcriptions"
        headers = {"Authorization": f"Bearer {api_key}"}
        with open(path, "rb") as f:
            files = {"file": (path.name, f, "application/octet-stream")}
            data = {"model": self.model}
            async with httpx.AsyncClient(timeout=120) as client:
                resp = await client.post(url, headers=headers, files=files, data=data)
                resp.raise_for_status()
        result = resp.json()
        text = result.get("text", "")
        if not text:
            logger.warning("Whisper API 返回空文本: {}", result)
        return text
