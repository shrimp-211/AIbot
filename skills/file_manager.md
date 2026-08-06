---
name: file-manager
description: 文件与目录管理
triggers: [文件, 目录, 保存文件, 读取文件, 查找文件, 内容, 写入]
tools: [file_read, file_write, glob, grep, bash, knowledge_add]
---

# 文件管理

- 处理文件读写、查找、搜索任务。
- 写文件前先确认目标路径合法(工作目录内)。
- 读大文件时按需读取(可用 glob 定位后再读)。
- 目录操作(创建/移动/删除)使用 bash,但危险命令会被权限系统拦截。
- 完成后汇报文件路径和内容摘要。
