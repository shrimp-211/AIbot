# QQ AI Agent 项目说明

## 架构
基于 Claude Code Agent 模式 + NoneBot2 适配器 + AstrBot 管道的万能 QQ AI Agent。

## 关键设计
- **adapter/**: OneBot v11 全双工 WebSocket 通信
- **pipeline/**: 7 阶段洋葱模型消息管道
- **agent/**: ReAct 循环 Agent 引擎 + 工具系统 + 技能 + 三层记忆
- **providers/**: OpenAI/Anthropic 统一 LLM 接口
- **plugins/**: 动态插件系统 (命令/消息装饰器注册)
- **security/**: 7 级权限 + 注入防护

## 运行
```bash
pip install -e .
python main.py
```

## 配置
编辑 config.yaml 设置 OneBot 连接、LLM Provider、权限等。

## 开发规范
- 使用 loguru 日志
- 异步优先，避免阻塞操作 — 文件 I/O 用 `asyncio.to_thread()` + 脏标记批量刷盘
- 工具权限分级 (0-7)
- 新功能优先以插件形式实现

## 安全规则 (从 .learnings/ 提升)

### 新增工具必须遵守
1. **网络工具**: 必须实现 URL 安全检查 — 阻止内网地址 (10.0.0.0/8, 127.0.0.0/8, 192.168.0.0/16, 169.254.0.0/16)
2. **文本匹配工具**: 正则表达式必须限制长度和检测危险回溯模式 (ReDoS)
3. **错误处理**: 永远向用户返回通用错误消息，用 `logger.exception()` 记录详情

### 常见陷阱
1. **初始化顺序**: 依赖对象必须先于引用者创建 (适配器→管道→回调)
2. **字典/bucket**: 所有 `defaultdict(list)` 必须有定期清理过期的逻辑
3. **事件循环**: 任何文件/网络 I/O 在 async 上下文中必须是异步的
4. **命名遮蔽**: 禁止模块级变量与函数同名 (如 `_sessions = {}` 被 `async def _sessions()` 覆盖)
5. **aiohttp API**: `web.Response(content_type="text/html", charset="utf-8")` — charset 独立参数

## 持续学习
- `.learnings/LEARNINGS.md` — 开发经验和最佳实践
- `.learnings/ERRORS.md` — 错误记录和修复
- `.learnings/FEATURE_REQUESTS.md` — 待实现的功能请求
