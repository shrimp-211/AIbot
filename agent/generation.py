"""AIGC 生成层:图片/视频/音频(TTS)/混合媒体合成。

- 参照 mainidea.txt 多模态生成层 + 用户新增需求(视频/语音/图片生成)。
- 各生成器惰性依赖:未配置 Key 或未装依赖时返回明确的能力缺失提示,不阻塞启动。
- 生成产物统一落盘到 output_dir,便于 QQ 富媒体(CQ 码)直接引用。
"""
from __future__ import annotations

import asyncio
import base64
import time
import urllib.parse
import uuid
from pathlib import Path
from typing import Any

from loguru import logger


def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def _stamp() -> str:
    return str(int(time.time() * 1000))


def _guess_ext(mime_or_url: str) -> str:
    m = (mime_or_url or "").lower()
    if "png" in m or m.endswith(".png"):
        return ".png"
    if "webp" in m or m.endswith(".webp"):
        return ".webp"
    if "jpg" in m or "jpeg" in m or m.endswith(".jpg"):
        return ".jpg"
    if "mp4" in m or m.endswith(".mp4"):
        return ".mp4"
    return ".bin"


class ImageGenerator:
    """图片生成:openai(DALL·E 等 OpenAI 兼容 images API)| pollinations(免费,无需 Key)。"""

    name = "image"

    def __init__(self, config: dict[str, Any], output_dir: Path):
        self.config = config or {}
        self.backend = (config or {}).get("backend", "pollinations")
        self.api_key = (config or {}).get("api_key", "")
        self.model = (config or {}).get("model", "dall-e-3")
        self.base_url = (config or {}).get("base_url", "")
        self.output_dir = output_dir
        _ensure_dir(output_dir)

    async def generate(self, prompt: str, size: str = "1024x1024") -> str:
        """生成图片,返回本地文件路径。

        适配度保障:主后端(openai)失败或无 Key 时,自动回退免费后端 pollinations,
        保证图片生成在无任何付费 Key 的环境下仍可用。
        """
        if not (prompt or "").strip():
            raise RuntimeError("图片生成需要 prompt 描述")
        primary = self._generate_openai if self.backend == "openai" else None
        if primary is not None:
            try:
                return await primary(prompt, size)
            except RuntimeError as exc:
                logger.warning("openai 图片生成失败,回退 pollinations: {}", exc)
            except Exception as exc:  # noqa: BLE001
                logger.warning("openai 图片生成异常,回退 pollinations: {}", exc)
        return await self._generate_pollinations(prompt, size)

    async def _generate_openai(self, prompt: str, size: str) -> str:
        if not self.api_key:
            raise RuntimeError("OpenAI 图片生成未配置 api_key(config.yaml 的 generation.image 段)")
        import httpx

        base = (self.base_url or "https://api.openai.com/v1").rstrip("/")
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                f"{base}/images/generations",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={"model": self.model, "prompt": prompt, "n": 1, "size": size},
            )
            resp.raise_for_status()
            data_list = resp.json().get("data") or []
            data = data_list[0] if data_list else {}
        if not data:
            raise RuntimeError("OpenAI 图片生成返回为空")
        out_path = self.output_dir / f"img_{_stamp()}.png"
        if data.get("b64_json"):
            await asyncio.to_thread(out_path.write_bytes, base64.b64decode(data["b64_json"]))
            return str(out_path)
        url = data.get("url")
        if url:
            return await self._download(url, "img", _guess_ext(url))
        raise RuntimeError("OpenAI 图片生成返回为空")

    async def _generate_pollinations(self, prompt: str, size: str) -> str:
        """pollinations.ai 免费图片 API:GET /prompt/{prompt} 直接返回图片字节。"""
        import httpx

        width, height = self._parse_size(size)
        url = (
            "https://image.pollinations.ai/prompt/"
            f"{urllib.parse.quote(prompt)}"
            f"?width={width}&height={height}&nologo=true"
        )
        return await self._download(url, "img", ".png")

    @staticmethod
    def _parse_size(size: str) -> tuple[int, int]:
        try:
            w, h = (size or "1024x1024").lower().split("x")
            return int(w), int(h)
        except (ValueError, AttributeError):
            return 1024, 1024

    async def _download(self, url: str, prefix: str, ext: str) -> str:
        import httpx

        out_path = self.output_dir / f"{prefix}_{_stamp()}{ext}"
        async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            await asyncio.to_thread(out_path.write_bytes, resp.content)
        return str(out_path)


class VideoGenerator:
    """视频生成(Runway Gen 系列,任务式 API:提交 → 轮询 → 下载)。

    未配置 Runway Key 时返回明确提示(避免误以为已支持)。
    """

    name = "video"

    def __init__(self, config: dict[str, Any], output_dir: Path):
        self.config = config or {}
        self.api_key = (config or {}).get("api_key", "")
        self.model = (config or {}).get("model", "gen3a_turbo")
        self.base_url = (config or {}).get("base_url", "https://api.dev.runwayml.com/v1")
        self.output_dir = output_dir
        _ensure_dir(output_dir)

    async def generate(
        self, prompt: str, image: str | None = None, duration: int = 5
    ) -> str:
        """生成视频,返回本地 mp4 路径。image 可选(图生视频)。"""
        if not self.api_key:
            raise RuntimeError(
                "视频生成需要 Runway API Key(config.yaml 的 generation.video.api_key);"
                "未配置时无法调用。"
            )
        if not (prompt or "").strip() and not image:
            raise RuntimeError("视频生成需要 prompt 或输入图片")
        import httpx

        async with httpx.AsyncClient(timeout=30) as client:
            headers = {"Authorization": f"Bearer {self.api_key}"}
            payload: dict[str, Any] = {"model": self.model, "promptText": prompt, "duration": int(duration)}
            if image:
                if image.startswith(("http://", "https://")):
                    payload["promptImage"] = image
                else:
                    payload["promptImage"] = await self._upload_image(client, headers, image)
            resp = await client.post(
                f"{self.base_url}/image_to_video", headers=headers, json=payload
            )
            resp.raise_for_status()
            task_id = resp.json().get("id")
            if not task_id:
                raise RuntimeError(f"Runway 未返回任务 id: {resp.json()}")
        # 轮询任务(最长 ~120s)
        video_url = await self._poll(task_id)
        return await self._download(video_url)

    async def _upload_image(self, client, headers: dict, path: str) -> str:
        import httpx

        # 按扩展名推断 MIME,避免 JPEG 图误标 image/png 导致 Runway 解析失败
        mime = "image/png"
        ext = Path(path).suffix.lower()
        if ext in (".jpg", ".jpeg"):
            mime = "image/jpeg"
        elif ext == ".webp":
            mime = "image/webp"
        with open(path, "rb") as f:
            files = {"file": (Path(path).name, f, mime)}
            resp = await client.post(f"{self.base_url}/files", headers=headers, files=files)
        resp.raise_for_status()
        return resp.json().get("id", "")

    async def _poll(self, task_id: str, timeout: int = 120) -> str:
        import httpx

        async with httpx.AsyncClient(timeout=30) as client:
            headers = {"Authorization": f"Bearer {self.api_key}"}
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                resp = await client.get(f"{self.base_url}/tasks/{task_id}", headers=headers)
                resp.raise_for_status()
                task = resp.json()
                status = (task.get("status") or "").upper()
                if status == "SUCCEEDED":
                    outputs = task.get("output") or []
                    if not outputs:
                        raise RuntimeError("Runway 任务成功但无输出")
                    first = outputs[0]
                    if isinstance(first, str):
                        return first
                    if isinstance(first, dict):
                        return first.get("url", "")
                    raise RuntimeError(f"Runway 输出格式异常: {first!r}")
                if status in ("FAILED", "CANCELLED", "THROTTLED"):
                    raise RuntimeError(f"Runway 任务失败: {status} {task.get('failure')}")
                await asyncio.sleep(3)
        raise RuntimeError("Runway 视频生成超时")

    async def _download(self, url: str) -> str:
        import httpx

        out_path = self.output_dir / f"video_{_stamp()}.mp4"
        async with httpx.AsyncClient(timeout=180, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            await asyncio.to_thread(out_path.write_bytes, resp.content)
        return str(out_path)


class AudioGenerator:
    """音频生成:语音合成(TTS,复用 I1 TTSProvider)+ 音乐生成(预留,需 Key)。"""

    name = "audio"

    def __init__(self, config: dict[str, Any], output_dir: Path, tts_provider: Any = None):
        self.config = config or {}
        self.output_dir = output_dir
        self.tts_provider = tts_provider
        self.music_cfg = (config or {}).get("music", {}) or {}
        _ensure_dir(output_dir)

    @property
    def tts_available(self) -> bool:
        return self.tts_provider is not None

    async def speak(self, text: str, voice: str | None = None) -> str:
        """文本转语音,返回音频文件路径。"""
        if self.tts_provider is None:
            raise RuntimeError(
                "语音合成未启用(config.yaml 的 provider_tts 段配置 edge 即可,免费)"
            )
        text = (text or "").strip()
        if not text:
            raise RuntimeError("语音合成需要文本")
        out_path = self.output_dir / f"tts_{_stamp()}.mp3"
        return await self.tts_provider.synthesize(
            text, str(out_path), voice=voice or self.config.get("voice")
        )

    async def music(self, prompt: str) -> str:
        """音乐生成:需 Suno/自建服务 Key,未配置时给出明确提示。"""
        key = self.music_cfg.get("api_key", "")
        if not key:
            raise RuntimeError(
                "音乐生成需要 music.api_key(config.yaml 的 generation.music 段),"
                "当前未配置。可使用语音合成(tts_speak)替代。"
            )
        raise RuntimeError("音乐生成接口未接入,请使用 tts_speak 语音合成")


class MixedMediaGenerator:
    """混合媒体合成:将 [文本, 图片prompt, 语音] 段落批量生成并汇总产物。"""

    name = "mixed"

    def __init__(self, image: ImageGenerator, audio: AudioGenerator):
        self.image = image
        self.audio = audio

    async def compose(self, segments: list[dict]) -> dict:
        """segments: [{text, image_prompt, image_size, speak}] → 各段媒体路径汇总。"""
        if not segments:
            raise RuntimeError("混合生成需要至少一个段落")
        results = []
        for i, seg in enumerate(segments):
            item: dict[str, Any] = {"index": i}
            if seg.get("text"):
                item["text"] = seg["text"]
            if seg.get("image_prompt"):
                item["image"] = await self.image.generate(
                    seg["image_prompt"], seg.get("image_size", "1024x1024")
                )
            if seg.get("speak"):
                item["voice"] = await self.audio.speak(seg["speak"], seg.get("voice"))
            results.append(item)
        return {"segments": results, "count": len(results)}


class GenerationManager:
    """统一生成入口:main.py 注入 AgentEngine,经 ToolContext.extra["generation"] 供工具调用。"""

    def __init__(self, config: dict[str, Any], tts_provider: Any = None, output_dir: str = "data/generated"):
        cfg = config or {}
        self.output_dir = Path(output_dir)
        _ensure_dir(self.output_dir)
        self.image = ImageGenerator(cfg.get("image") or {}, self.output_dir)
        self.video = VideoGenerator(cfg.get("video") or {}, self.output_dir)
        self.audio = AudioGenerator(cfg, self.output_dir, tts_provider)
        self.mixed = MixedMediaGenerator(self.image, self.audio)

    def stats(self) -> dict:
        return {
            "image_backend": self.image.backend,
            "video_key": bool(self.video.api_key),
            "tts": self.audio.tts_available,
        }
