"""LiveChatService(本项目适配):WebSocket 聊天(文本 + 实时语音)。

- ``unified-chat/ws``:文本聊天,直接调用本项目 AgentEngine(流式)
- ``live-chat/ws``:语音聊天,STT(本项目 Whisper)→ 引擎 → TTS(本项目 Edge TTS)→ 音频回传
"""

from __future__ import annotations

import asyncio
import base64
import os
import re
import uuid
import wave
from typing import Any

from astrbot.core import logger
from astrbot.core.core_lifecycle import AstrBotCoreLifecycle
from astrbot.core.utils.astrbot_path import get_astrbot_temp_path


class LiveChatServiceError(Exception):
    pass


class LiveChatService:
    def __init__(self, db, core_lifecycle: AstrBotCoreLifecycle) -> None:
        self.db = db
        self.core_lifecycle = core_lifecycle
        self.engine = getattr(core_lifecycle, "_engine", None)
        self.config = getattr(core_lifecycle, "astrbot_config", None)
        self.stt = getattr(core_lifecycle, "stt_provider", None)
        self.tts = getattr(core_lifecycle, "tts_provider", None)

    # ================= 入口 =================

    async def run_websocket_session(
        self,
        token: str | None,
        force_ct: str | None,
        receive_json,
        send_json,
        close,
    ) -> None:
        """运行一个 WebSocket 聊天会话。"""
        try:
            await send_json({"type": "session_ready", "session_id": "live"})
        except Exception:
            return
        audio_buffer: list[bytes] = []
        try:
            while True:
                try:
                    msg = await receive_json()
                except Exception:
                    break
                if not isinstance(msg, dict):
                    continue
                t = msg.get("t") or msg.get("type")
                if t == "start_speaking":
                    audio_buffer.clear()
                    continue
                if t == "speaking_part":
                    raw = msg.get("audio") or msg.get("data") or ""
                    if raw:
                        try:
                            audio_buffer.append(base64.b64decode(raw))
                        except Exception:
                            pass
                    continue
                if t == "end_speaking":
                    pcm = b"".join(audio_buffer)
                    audio_buffer.clear()
                    await self._handle_voice(pcm, send_json)
                    continue
                if t == "interrupt":
                    continue
                # 文本消息
                if "message" in msg or "text" in msg:
                    await self._handle_text(msg, send_json)
        except asyncio.CancelledError:
            pass
        except Exception as exc:  # noqa: BLE001
            logger.error("Live Chat WS 错误: %s", exc, exc_info=True)
            try:
                await close(1011, "internal error")
            except Exception:
                pass

    # ================= 文本聊天 =================

    async def _handle_text(self, msg: dict, send_json) -> None:
        message = msg.get("message", msg.get("text", ""))
        if isinstance(message, list):
            message = "".join(
                str(p.get("text", "") if isinstance(p, dict) else p) for p in message
            )
        text = str(message or "").strip()
        if not text:
            return
        message_id = str(msg.get("message_id") or uuid.uuid4())
        session_id = str(msg.get("session_id") or "default")
        await send_json({"type": "message_saved", "data": {"id": message_id}})
        reply = await self._run_engine(text, send_json)
        await send_json({"type": "complete", "data": reply or "", "message_id": message_id})
        await send_json({"type": "end", "message_id": message_id})

    async def _run_engine(self, text: str, send_json) -> str:
        """调用本项目引擎,流式回传。返回最终回复。"""
        if self.engine is None:
            return "引擎未配置,无法聊天。"
        queue: asyncio.Queue = asyncio.Queue()

        def _make_event():
            safe_user = "webui_live"
            from src.adapter.event import AgentEvent
            from src.adapter.message import MessageChain, MessageSegment

            event = AgentEvent(
                message_type="private",
                user_id=safe_user,
                sender_name=safe_user,
                session_id=f"webui:{safe_user}:live",
                message=MessageChain([MessageSegment.text(text)]),
                is_tome=True,
            )

            def _stream(content: str, reasoning: str) -> None:
                if content:
                    queue.put_nowait(("plain", content))
                elif reasoning:
                    queue.put_nowait(("reasoning", reasoning))

            event._stream_callback = _stream
            event._tool_callback = lambda *a: None
            event._send_callback = lambda *a, **k: None
            return event

        event = _make_event()

        async def _run() -> str:
            try:
                reply = await self.engine.process(event) or ""
            except Exception as exc:  # noqa: BLE001
                logger.error("Live Chat 引擎错误: %s", exc, exc_info=True)
                reply = "处理出错,请查看服务端日志。"
            queue.put_nowait(("__final__", reply))
            return reply

        task = asyncio.create_task(_run())
        parts: list[str] = []
        final_reply = ""
        while True:
            kind, data = await queue.get()
            if kind == "__final__":
                final_reply = data or ""
                break
            if kind == "plain":
                parts.append(data)
                try:
                    await send_json({"type": "plain", "data": data, "streaming": True})
                except Exception:
                    break
            elif kind == "reasoning":
                try:
                    await send_json({"type": "plain", "chain_type": "reasoning", "data": data})
                except Exception:
                    break
        reply = final_reply or "".join(parts)
        return reply

    # ================= 语音聊天 =================

    async def _handle_voice(self, pcm: bytes, send_json) -> None:
        """语音:STT → 引擎 → TTS。"""
        try:
            text = await self._stt_from_pcm(pcm)
        except Exception as exc:  # noqa: BLE001
            logger.error("STT 失败: %s", exc)
            await send_json({"type": "error", "data": "语音识别失败"})
            return
        if not text:
            await send_json({"type": "error", "data": "未识别到语音"})
            return
        await send_json({"type": "stt", "data": text})
        reply = await self._run_engine(text, send_json)
        await send_json({"type": "complete", "data": reply})
        # TTS 回传
        if self.tts is not None:
            try:
                audio = await self._tts_to_pcm(reply)
                if audio:
                    await send_json({"type": "speaking_part", "audio": base64.b64encode(audio).decode()})
            except Exception as exc:  # noqa: BLE001
                logger.error("TTS 失败: %s", exc)
        await send_json({"type": "end"})

    async def _stt_from_pcm(self, pcm: bytes) -> str:
        if self.stt is None:
            raise RuntimeError("STT Provider 未配置")
        # PCM16 16kHz mono → wav
        tmp_dir = os.path.join(get_astrbot_temp_path(), "livechat")
        os.makedirs(tmp_dir, exist_ok=True)
        wav_path = os.path.join(tmp_dir, f"{uuid.uuid4().hex}.wav")
        with wave.open(wav_path, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            wf.writeframes(pcm)
        try:
            return await self.stt.transcribe(wav_path) or ""
        finally:
            if os.path.exists(wav_path):
                os.remove(wav_path)

    async def _tts_to_pcm(self, text: str) -> bytes | None:
        if self.tts is None:
            return None
        tmp_dir = os.path.join(get_astrbot_temp_path(), "livechat")
        os.makedirs(tmp_dir, exist_ok=True)
        out_path = os.path.join(tmp_dir, f"{uuid.uuid4().hex}.mp3")
        try:
            await self.tts.synthesize(text, out_path)
            with open(out_path, "rb") as f:
                return f.read()
        finally:
            if os.path.exists(out_path):
                os.remove(out_path)
