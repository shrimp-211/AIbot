"""第 5 阶段: 核心处理 — 插件匹配 → Agent 引擎.

这是管道中最关键的阶段:
1. 先尝试匹配插件 (通过 PluginRegistry)
2. 如果无插件匹配，使用 Agent 引擎处理
3. 结果写入 event._reply
"""

from loguru import logger


class ProcessStage:
    """核心处理阶段."""

    def __init__(self, engine, plugins, cfg):
        self.engine = engine      # AgentEngine 实例
        self.plugins = plugins    # PluginRegistry 实例
        self.cfg = cfg

    async def process(self, event):
        text = event.text.strip()
        if not text:
            return

        # 1. 尝试插件匹配
        plugin_result = await self.plugins.dispatch(event)
        if plugin_result is not None:
            # 插件已处理，且结果是 ProviderRequest → 调用 LLM
            if plugin_result == "__LLM__":
                reply = await self.engine.process(
                    message=text,
                    session_id=event.get_session_id(),
                    user_id=event.user_id,
                    user_name=event.user_name,
                )
                event.reply(reply)
            else:
                event.reply(str(plugin_result))
            return

        # 2. 无插件匹配 → Agent 引擎处理
        reply = await self.engine.process(
            message=text,
            session_id=event.get_session_id(),
            user_id=event.user_id,
            user_name=event.user_name,
        )
        event.reply(reply)
