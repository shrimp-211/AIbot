"""Provider 基类 — 统一的 LLM 调用接口."""

from abc import ABC, abstractmethod
from typing import Any


class BaseProvider(ABC):
    """LLM 提供商基类.

    所有 LLM 提供商需要实现 chat() 方法，
    返回统一格式的响应字典:
      {"content": "文本回复"}
      或 (有工具调用时)
      {"content": None, "tool_calls": [...]}
    """

    def __init__(self, model: str, api_key: str, base_url: str, **kwargs):
        self.model = model
        self.api_key = api_key
        self.base_url = base_url
        self.extra = kwargs

    @abstractmethod
    async def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        **kwargs,
    ) -> dict[str, Any]:
        """调用 LLM 进行对话.

        Args:
            messages: 消息列表
            tools: 工具定义列表 (OpenAI Function Calling 格式)

        Returns:
            {"content": str, "tool_calls": list | None}
        """
        ...
