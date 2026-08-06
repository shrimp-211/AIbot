"""洋葱模型管道调度器。

参考 AstrBot 的 PipelineScheduler:async generator 的 yield 前为前置处理,
yield 后为后置处理,递归调用后续阶段。

中止约定:阶段通过设置 `event.is_stopped` 来跳过后续阶段(不抛异常)。
阶段抛出的异常会向上传播,终止该事件整个管道(有意为之,由调用方捕获);
因此阶段内部应自行 try/except 消化可预期的错误。
"""
from __future__ import annotations

import inspect
from abc import ABC
from typing import Any, AsyncGenerator, Awaitable, Callable, Union

from ..adapter.event import AgentEvent

StageProcess = Union[Awaitable[None], AsyncGenerator[None, None]]


class Stage(ABC):
    """管道阶段基类。process() 可返回协程或 async generator。"""

    async def initialize(self, ctx: Any) -> None:
        pass

    async def process(self, event: AgentEvent) -> StageProcess:
        """处理事件。普通协程顺序执行;async generator 的 yield 前后分界。"""
        return None


class PipelineScheduler:
    def __init__(self, stages: list[Stage] | None = None):
        self.stages: list[Stage] = list(stages or [])

    def add_stage(self, stage: Stage) -> None:
        self.stages.append(stage)

    def add_stages(self, stages: list[Stage]) -> None:
        self.stages.extend(stages)

    async def initialize(self, ctx: Any = None) -> None:
        for stage in self.stages:
            await stage.initialize(ctx)

    async def execute(self, event: AgentEvent) -> None:
        if event.is_stopped:
            return
        await self._process_stages(event, 0)

    async def _process_stages(self, event: AgentEvent, from_stage: int) -> None:
        for i in range(from_stage, len(self.stages)):
            stage = self.stages[i]
            result = stage.process(event)
            if inspect.isasyncgen(result):
                # 洋葱模型:yield 前执行前置逻辑,然后递归跑后续阶段,再回到 yield 后
                async for _ in result:
                    if event.is_stopped:
                        break
                    await self._process_stages(event, i + 1)
                    if event.is_stopped:
                        break
            else:
                await result
                if event.is_stopped:
                    break
