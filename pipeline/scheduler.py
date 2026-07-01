"""管道调度器 — 基于 AstrBot 洋葱模型实现的 7 阶段消息管道.

每个阶段可以是:
- 普通协程: 顺序执行，不可中断
- 异步生成器 (async generator): 实现洋葱模型 — yield 前是前置处理，
  yield 后是后置处理，中间递归执行后续阶段。

阶段顺序:
1. WakeCheck      — @检测 / 唤醒词 / 私聊判断
2. RateLimit      — 频率限制
3. ContentSafety  — 内容安全检查
4. PreProcess     — 命令解析 / 多模态解析
5. Process        — 核心: 插件匹配 → Agent 引擎
6. Decorate       — 结果后处理 / 文本分段
7. Respond        — 通过适配器发送回复
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator

from loguru import logger


class PipelineScheduler:
    """管道调度器 — 按顺序执行各阶段."""

    def __init__(self):
        self._stages: list = []

    def add_stage(self, stage) -> None:
        """添加处理阶段."""
        self._stages.append(stage)

    async def execute(self, event) -> None:
        """执行完整管道.

        Args:
            event: AgentEvent 实例
        """
        logger.debug(f"管道开始: {event.text[:50] if event.text else '(无文本)'}")

        try:
            for i, stage in enumerate(self._stages):
                result = stage.process(event)

                if isinstance(result, AsyncGenerator):
                    # 洋葱模型: 异步生成器
                    async for _ in result:
                        if event.is_stopped():
                            break
                else:
                    # 普通协程
                    await result

                if event.is_stopped():
                    logger.debug(f"管道终止于阶段 {i}: {stage.__class__.__name__}")
                    break

        except Exception:
            logger.exception("管道执行异常")

        logger.debug("管道完成")
