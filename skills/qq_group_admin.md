---
name: qq-group-admin
description: QQ 群管理与运营
triggers: [踢人, 禁言, 群公告, 群管理, 管理群, 精华, 群文件, 设置管理]
tools: [qq_kick, qq_mute, qq_set_admin, qq_group_announce, qq_essence, qq_group_info, qq_group_list, qq_group_file_list, qq_send_image, qq_send_voice]
---

# QQ 群管理

- 本技能用于 QQ 群管理操作:踢人、禁言、设置管理、公告、精华、群文件。
- 所有管理操作均有权限等级要求,执行前先确认调用者角色等级。
- 敏感操作(踢人/禁言)前说明后果,等待用户确认。
- 操作完成后用简洁文字汇报结果。
- 群文件/群信息查询可直接返回结果。
