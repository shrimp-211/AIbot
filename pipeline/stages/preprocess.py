"""第 4 阶段: 预处理 — 命令解析与多模态解析."""

from loguru import logger


class PreProcessStage:
    """消息预处理.

    负责:
    - 命令前缀检测 (如 /help → 直接返回帮助文本)
    - 多模态内容解析 (图片/文件等)
    """

    def __init__(self, cfg):
        self.cfg = cfg
        self._prefix = cfg.get("permissions.command_prefix", "/")

    async def process(self, event):
        text = event.text.strip()

        # 无内容则忽略
        if not text:
            event.stop()
            return

        # 命令前缀检测: /开头的短命令直接响应
        if text.startswith(self._prefix) and len(text) < 100:
            # 这些命令由 ProcessStage 的 NLU 快速路径处理
            # 这里只做标记
            event.state["is_command"] = True

        # 多模态解析 (图片/语音等)
        images = event.message_chain.filter("image")
        records = event.message_chain.filter("record")

        if images:
            event.state["images"] = [seg.data.get("url", "") for seg in images]
        if records:
            event.state["records"] = [seg.data.get("url", "") for seg in records]
