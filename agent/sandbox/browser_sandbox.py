"""浏览器沙箱:Playwright headless(惰性依赖)。

未安装 playwright 时返回明确提示,不阻塞启动。
"""
from __future__ import annotations

from typing import Any

from loguru import logger


class BrowserSandbox:
    """Playwright headless 浏览器,会话级复用 context。"""

    def __init__(self, session_id: str = "default"):
        self.session_id = session_id
        self._browser = None
        self._context = None

    async def open(self, url: str, timeout: int = 30) -> dict[str, Any]:
        """打开 URL 并返回页面文本摘要(截断),失败返回明确错误。"""
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            return {"error": "浏览器沙箱需要 playwright(pip install playwright && playwright install chromium)"}
        try:
            p = await async_playwright().start()
            if self._browser is None:
                self._browser = await p.chromium.launch(headless=True)
            if self._context is None:
                self._context = await self._browser.new_context(user_agent="QQBot-Browser/1.0")
            page = await self._context.new_page()
            try:
                await page.goto(url, timeout=int(timeout) * 1000, wait_until="domcontentloaded")
                text = await page.evaluate("document.body ? document.body.innerText : ''")
                title = await page.title()
            finally:
                await page.close()
            return {"title": title, "text": (text or "")[:4000], "url": url}
        except Exception as exc:  # noqa: BLE001
            logger.warning("浏览器沙箱访问失败: {}", exc)
            return {"error": f"浏览器访问失败: {exc}"}

    async def close(self) -> None:
        if self._context is not None:
            try:
                await self._context.close()
            except Exception:  # noqa: BLE001
                pass
            self._context = None
        if self._browser is not None:
            try:
                await self._browser.close()
            except Exception:  # noqa: BLE001
                pass
            self._browser = None
