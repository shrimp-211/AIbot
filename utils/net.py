"""安全网络请求工具:逐跳 SSRF 校验(防重定向到内网)。

所有对外发起 URL 请求的入口(感知工具下载 / 知识库 URL 入库 / 网页抓取)
都应经 safe_fetch_url,避免跟随 302 跳转到内网地址后被回显。
"""
from __future__ import annotations

from typing import Any

from loguru import logger


async def is_safe_url(url: str) -> bool:
    """SSRF 安全校验(同步解析版本,封装 auth.is_safe_url_async)。"""
    from ..security.auth import is_safe_url_async

    try:
        return await is_safe_url_async(url)
    except Exception:  # noqa: BLE001
        return False


async def safe_fetch_url(
    url: str,
    timeout: float = 30.0,
    headers: dict[str, str] | None = None,
    max_redirects: int = 5,
    method: str = "GET",
) -> Any:
    """逐跳 SSRF 校验后抓取 URL,返回 httpx.Response。

    - 初始 URL 与每一跳重定向目标都经 is_safe_url_async 校验,校验失败抛 RuntimeError
    - 不自动跟随重定向,手动逐跳校验(防 302 → 内网)
    """
    import httpx

    from ..security.auth import is_safe_url_async

    if not await is_safe_url_async(url):
        raise RuntimeError(f"URL 安全检查未通过: {url}")
    current = url
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
        for _ in range(max_redirects + 1):
            resp = await client.request(method, current, headers=headers)
            if resp.is_redirect and resp.headers.get("location"):
                location = resp.headers["location"]
                next_url = str(httpx.URL(location).join(str(resp.url)))
                if not await is_safe_url_async(next_url):
                    raise RuntimeError(f"重定向目标安全检查未通过: {next_url}")
                logger.debug("SSRF 校验通过,跟随重定向: {}", next_url)
                current = next_url
                continue
            return resp
    raise RuntimeError("重定向次数过多")
