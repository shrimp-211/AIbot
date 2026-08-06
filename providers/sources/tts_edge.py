"""Edge TTS 文字转语音。基于微软 Edge 在线语音(免费),无需 API Key。

config: {type: edge, voice: zh-CN-XiaoxiaoNeural, rate: +0%, volume: +0%}
更多语音见 https://github.com/rany2/edge-tts 的 voices 列表。
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from ..base import TTSProvider


class EdgeTTSProvider(TTSProvider):
    name = "edge"

    async def synthesize(self, text: str, output_path: str, **kwargs: Any) -> str:
        import edge_tts

        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        voice = kwargs.get("voice") or self.config.get("voice", "zh-CN-XiaoxiaoNeural")
        rate = kwargs.get("rate") or self.config.get("rate", "+0%")
        volume = kwargs.get("volume") or self.config.get("volume", "+0%")

        def _run() -> None:
            # edge-tts 内部是 asyncio 实现,在 to_thread 里用独立事件循环
            asyncio.run(
                edge_tts.Communicate(
                    text, voice=voice, rate=rate, volume=volume
                ).save(str(out))
            )

        await asyncio.to_thread(_run)
        if not out.is_file() or out.stat().st_size == 0:
            raise RuntimeError(f"语音合成失败,未生成有效文件: {output_path}")
        return str(out)
