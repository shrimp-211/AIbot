"""QQ群文件工具 — 上传/下载/列表."""

from typing import Any
from .base import BaseTool


class QQGroupFileListTool(BaseTool):
    name = "qq_group_file_list"
    description = "列出QQ群文件"
    permission_level = 0
    adapter = None

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {
            "group_id": {"type": "string", "description": "群号"},
        }, "required": ["group_id"]}

    async def execute(self, group_id: str, **kwargs) -> str:
        if not self.adapter: return "QQ适配器未连接"
        result = await self.adapter.call_api("get_group_file_list", {"group_id": int(group_id)})
        if result and result.get("status") == "ok":
            files = result.get("data", {}).get("files", [])
            if not files: return f"群 {group_id} 暂无文件"
            return "群文件:\n" + "\n".join(f"  {f.get('file_name','?')} ({f.get('size',0)}B)" for f in files[:20])
        return f"获取失败: {result}"
