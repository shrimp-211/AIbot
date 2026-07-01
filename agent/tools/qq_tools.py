"""QQ 原生操作工具集 — 通过 OneBot v11 API 暴露 QQ 全部能力.

支持的操作:
- 群管理: 踢人/禁言/设管理/群信息/群列表/群公告
- 消息管理: 撤回/点赞/发图/发语音/发音乐
- 好友管理: 好友列表/陌生人信息
- 群文件: 上传/下载/列表
- 精华消息: 设置/移除
"""

from __future__ import annotations

from typing import Any

from .base import BaseTool


class QQGroupInfoTool(BaseTool):
    name = "qq_group_info"
    description = "获取QQ群信息: 群名称、成员数、群主、公告等"
    permission_level = 0

    def __init__(self):
        super().__init__()
        self.adapter = None

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "group_id": {"type": "string", "description": "群号"},
            },
            "required": ["group_id"],
        }

    async def execute(self, group_id: str, **kwargs) -> str:
        if not self.adapter:
            return "QQ适配器未连接"
        result = await self.adapter.call_api("get_group_info", {"group_id": int(group_id)})
        if result and result.get("status") == "ok":
            data = result.get("data", {})
            return f"群 {group_id} 信息: 名称={data.get('group_name','?')}, 成员数={data.get('member_count','?')}, 群主={data.get('owner_id','?')}"
        return f"获取群信息失败: {result}"


class QQGroupListTool(BaseTool):
    name = "qq_group_list"
    description = "获取机器人加入的所有QQ群列表"
    def __init__(self):
        super().__init__()
        self.adapter = None
    permission_level =1
    adapter = None

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def execute(self, **kwargs) -> str:
        if not self.adapter:
            return "QQ适配器未连接"
        result = await self.adapter.call_api("get_group_list", {})
        if result and result.get("status") == "sent":
            return "已请求群列表"
        return f"获取群列表失败"


class QQKickTool(BaseTool):
    name = "qq_kick"
    description = "踢出QQ群成员 (需要管理员权限)"
    def __init__(self):
        super().__init__()
        self.adapter = None
    permission_level =7
    adapter = None

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "group_id": {"type": "string", "description": "群号"},
                "user_id": {"type": "string", "description": "要踢出的用户QQ号"},
                "reject_add": {"type": "boolean", "description": "是否拒绝再次加群", "default": False},
            },
            "required": ["group_id", "user_id"],
        }

    async def execute(self, group_id: str, user_id: str, reject_add: bool = False, **kwargs) -> str:
        if not self.adapter:
            return "QQ适配器未连接"
        result = await self.adapter.call_api("set_group_kick", {
            "group_id": int(group_id),
            "user_id": int(user_id),
            "reject_add_request": reject_add,
        })
        if result and result.get("status") == "ok":
            return f"已踢出用户 {user_id} 从群 {group_id}"
        return f"踢出失败: {result}"


class QQMuteTool(BaseTool):
    name = "qq_mute"
    description = "禁言QQ群成员 (需要管理员权限)。duration=0 表示解除禁言。"
    def __init__(self):
        super().__init__()
        self.adapter = None
    permission_level =4
    adapter = None

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "group_id": {"type": "string", "description": "群号"},
                "user_id": {"type": "string", "description": "用户QQ号"},
                "duration": {"type": "integer", "description": "禁言时长(秒), 0=解除", "default": 60},
            },
            "required": ["group_id", "user_id", "duration"],
        }

    async def execute(self, group_id: str, user_id: str, duration: int = 60, **kwargs) -> str:
        if not self.adapter:
            return "QQ适配器未连接"
        await self.adapter.call_api("set_group_ban", {
            "group_id": int(group_id),
            "user_id": int(user_id),
            "duration": duration,
        })
        action = f"禁言 {duration} 秒" if duration > 0 else "解除禁言"
        return f"已在群 {group_id} 对 {user_id} {action}"


class QQSetAdminTool(BaseTool):
    name = "qq_set_admin"
    description = "设置或取消QQ群管理员 (需要群主权限)"
    def __init__(self):
        super().__init__()
        self.adapter = None
    permission_level =7
    adapter = None

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "group_id": {"type": "string", "description": "群号"},
                "user_id": {"type": "string", "description": "用户QQ号"},
                "enable": {"type": "boolean", "description": "True=设为管理, False=取消管理"},
            },
            "required": ["group_id", "user_id", "enable"],
        }

    async def execute(self, group_id: str, user_id: str, enable: bool = True, **kwargs) -> str:
        if not self.adapter:
            return "QQ适配器未连接"
        await self.adapter.call_api("set_group_admin", {
            "group_id": int(group_id),
            "user_id": int(user_id),
            "enable": enable,
        })
        action = "设为管理员" if enable else "取消管理员"
        return f"已在群 {group_id} 将 {user_id} {action}"


class QQSendImageTool(BaseTool):
    name = "qq_send_image"
    description = "发送图片到QQ群或私聊。支持URL、本地路径或Base64。"
    def __init__(self):
        super().__init__()
        self.adapter = None
    permission_level =0
    adapter = None

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "group_id": {"type": "string", "description": "群号 (群聊时必填)"},
                "user_id": {"type": "string", "description": "用户QQ号 (私聊时必填)"},
                "image_url": {"type": "string", "description": "图片URL"},
                "text": {"type": "string", "description": "附带文字", "default": ""},
            },
            "required": ["image_url"],
        }

    async def execute(self, image_url: str, group_id: str = "", user_id: str = "", text: str = "", **kwargs) -> str:
        if not self.adapter:
            return "QQ适配器未连接"
        cq_msg = f"[CQ:image,file={image_url}]"
        if text:
            cq_msg = text + cq_msg
        success = await self.adapter.send_raw(cq_msg, user_id=user_id, group_id=group_id)
        return "图片已发送" if success else "发送失败"


class QQSendVoiceTool(BaseTool):
    name = "qq_send_voice"
    description = "发送语音消息到QQ"
    def __init__(self):
        super().__init__()
        self.adapter = None
    permission_level =1
    adapter = None

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "group_id": {"type": "string", "description": "群号"},
                "voice_url": {"type": "string", "description": "语音文件URL"},
            },
            "required": ["group_id", "voice_url"],
        }

    async def execute(self, group_id: str, voice_url: str, **kwargs) -> str:
        if not self.adapter:
            return "QQ适配器未连接"
        success = await self.adapter.send_raw(
            f"[CQ:record,file={voice_url}]", group_id=group_id
        )
        return "语音已发送" if success else "发送失败"


class QQRecallTool(BaseTool):
    name = "qq_recall"
    description = "撤回QQ消息 (需要管理员权限)"
    def __init__(self):
        super().__init__()
        self.adapter = None
    permission_level =4
    adapter = None

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "message_id": {"type": "string", "description": "要撤回的消息ID (可以从事件中获取)"},
            },
            "required": ["message_id"],
        }

    async def execute(self, message_id: str, **kwargs) -> str:
        if not self.adapter:
            return "QQ适配器未连接"
        await self.adapter.call_api("delete_msg", {"message_id": int(message_id)})
        return f"已请求撤回消息 {message_id}"


class QQSendLikeTool(BaseTool):
    name = "qq_send_like"
    description = "给QQ用户点赞"
    def __init__(self):
        super().__init__()
        self.adapter = None
    permission_level =0
    adapter = None

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "user_id": {"type": "string", "description": "用户QQ号"},
                "times": {"type": "integer", "description": "点赞次数 (默认1，最大10)", "default": 1},
            },
            "required": ["user_id"],
        }

    async def execute(self, user_id: str, times: int = 1, **kwargs) -> str:
        if not self.adapter:
            return "QQ适配器未连接"
        await self.adapter.call_api("send_like", {
            "user_id": int(user_id),
            "times": min(times, 10),
        })
        return f"已给 {user_id} 点赞 {min(times, 10)} 次"


class QQFriendListTool(BaseTool):
    name = "qq_friend_list"
    description = "获取QQ好友列表"
    def __init__(self):
        super().__init__()
        self.adapter = None
    permission_level =1
    adapter = None

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def execute(self, **kwargs) -> str:
        if not self.adapter:
            return "QQ适配器未连接"
        result = await self.adapter.call_api("get_friend_list", {})
        if result and result.get("status") == "sent":
            return "已请求好友列表"
        return "获取好友列表失败"


class QQEssenceTool(BaseTool):
    name = "qq_essence"
    description = "设置或移除QQ群精华消息 (需要管理员权限)"
    def __init__(self):
        super().__init__()
        self.adapter = None
    permission_level =4
    adapter = None

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "message_id": {"type": "string", "description": "消息ID"},
                "action": {"type": "string", "enum": ["set", "remove"], "description": "set=设精华, remove=取消精华"},
            },
            "required": ["message_id", "action"],
        }

    async def execute(self, message_id: str, action: str = "set", **kwargs) -> str:
        if not self.adapter:
            return "QQ适配器未连接"
        api = "set_essence_msg" if action == "set" else "delete_essence_msg"
        await self.adapter.call_api(api, {"message_id": int(message_id)})
        return f"已{'设置' if action == 'set' else '取消'}精华消息 {message_id}"


class QQGroupAnnounceTool(BaseTool):
    name = "qq_announce"
    description = "发送QQ群公告 (需要管理员权限)"
    def __init__(self):
        super().__init__()
        self.adapter = None
    permission_level =4
    adapter = None

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "group_id": {"type": "string", "description": "群号"},
                "content": {"type": "string", "description": "公告内容"},
            },
            "required": ["group_id", "content"],
        }

    async def execute(self, group_id: str, content: str, **kwargs) -> str:
        if not self.adapter:
            return "QQ适配器未连接"
        await self.adapter.call_api("_send_group_notice", {
            "group_id": int(group_id),
            "content": content,
        })
        return f"已在群 {group_id} 发送公告"


class QQStrangerInfoTool(BaseTool):
    name = "qq_stranger_info"
    description = "获取QQ陌生人信息 (昵称、性别、年龄等)"
    def __init__(self):
        super().__init__()
        self.adapter = None
    permission_level =0
    adapter = None

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "user_id": {"type": "string", "description": "用户QQ号"},
            },
            "required": ["user_id"],
        }

    async def execute(self, user_id: str, **kwargs) -> str:
        if not self.adapter:
            return "QQ适配器未连接"
        result = await self.adapter.call_api("get_stranger_info", {"user_id": int(user_id)})
        if result and result.get("status") == "sent":
            return f"已请求用户 {user_id} 的信息"
        return f"获取用户信息失败"


class QQSignInTool(BaseTool):
    name = "qq_sign_in"
    description = "QQ群签到"
    def __init__(self):
        super().__init__()
        self.adapter = None
    permission_level =0
    adapter = None

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "group_id": {"type": "string", "description": "群号"},
            },
            "required": ["group_id"],
        }

    async def execute(self, group_id: str, **kwargs) -> str:
        if not self.adapter:
            return "QQ适配器未连接"
        await self.adapter.call_api("send_group_sign", {"group_id": int(group_id)})
        return f"已在群 {group_id} 签到"
