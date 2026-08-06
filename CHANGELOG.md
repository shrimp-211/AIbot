# 更新日志

本项目的变更记录。格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/)。

## [1.2.0] - 2026-08-06

### 新增
- **反向驱动 `ReverseDriver`**(参考 NoneBot Driver 抽象):单个共享 aiohttp 应用承载多适配器的 WebSocket / HTTP 路由,被动接收类适配器不再各自开端口;`config.yaml` 新增 `driver` 段(默认绑定 6199,`enabled: false` 可回退到旧的每适配器自持端口)
- **适配器插件化加载**:`data/adapters/*.py` 提供 `register(registry, config, driver)` 入口,新增平台只需放入一个插件文件,无需修改 `main.py`(内置 5 个适配器插件:反向 WS / 正向 WS / HTTP / QQ 官方 / Telegram)
- **`AdapterRegistry` 依赖注入**:插件可通过 `registry: AdapterRegistry` 参数调用平台 API,支撑群管等平台操作插件
- **移植 5 个 NoneBot 风格插件**(置于 `data/plugins/`):
  - 翻译(`/翻译`,Google gtx 免费接口,自动识别语种)
  - 群管理(`/禁言 /解禁 /踢人 /设管理 /全体禁言`,管理员权限,经注入的 AdapterRegistry 调 OneBot API)
  - 复读机(群内同一非命令消息连续 3 次自动复读,JsonKV 持久化)
  - 计算器(`/计算`,基于 ast 白名单的安全求值,幂/函数/防溢出)
  - 诗词(`/诗词`,今日诗词 API + 本地兜底)

### 变更
- **管道重构(NoneBot 风格插件分发)**:阶段顺序调整为 Notice → RateLimit → ContentSafety → Security → Plugin → WakeCheck → PreProcess → Process → Decorate → Respond
  - 新增 `SecurityStage`:黑名单 / 私聊配对 / 群白名单(从原 WakeCheckStage 抽出)
  - 新增 `PluginStage`:置于唤醒检测之前,所有消息先经插件分发,message/regex/command handler 无需 @ 唤醒即可生效
  - `WakeCheckStage` 简化为纯唤醒检测(仅 @/命令前缀/唤醒词)
  - `ProcessStage` 改为仅 Agent(插件分发已前移)

## [1.1.0] - 2026-08-06

### 新增
- **通知事件系统**:OneBot notice/request 事件解析(`group_increase/group_decrease/group_recall`、加好友/加群请求),`AgentEvent` 新增 `event_type/notice_type/sub_type/operator_id/flag` 字段
- **NoticeStage**:群欢迎/欢送、防撤回(适配器消息缓存恢复原文)、好友/加群请求自动审批策略(`config.yaml` 的 `notice` 段)
- **并行工具执行**:ReAct 循环内多个工具调用通过 `asyncio.gather` 并发执行(参考 Claude Code/Codex)
- **`file_edit` 工具**:文件内精确查找替换,`old_string` 必须唯一匹配
- **引擎统计**:`messages_processed` 计数 + `stats()`,接入 WebUI `/api/status`
- **移植 5 个经典插件**(参考 NoneBot2/AstrBot 生态,均置于 `data/plugins/`):
  - 每日签到(积分/连续签到/排行,JsonKV 持久化)
  - 掷骰子/抽签(`/roll 2d6+3` / `/抽签`)
  - 一言(在线接口 + 本地兜底)
  - 天气查询(wttr.in 免费接口)
  - 关键词自动回复(管理员配置,消息命中自动应答)
- **插件元数据**:`plugin.json`(name/version/author/description/commands/dependencies),`/plugins` 命令展示

### 修复
- **依赖注入支持字符串注解**:外部插件模块启用 `from __future__ import annotations` 时,`resolve_params` 无法将 `db: JsonKV`/`config: Config` 匹配到已注册依赖,导致注入静默失效
- **天气插件**:wttr.in 以 `text/plain` 返回 JSON,改用 `resp.json(content_type=None)`

## [1.0.0] - 2026-08-06

### Phase 1:核心集成与 Bug 修复
- 上下文压缩接入 ReAct 循环(超阈值自动压缩 + pre/post_compaction 钩子)
- Skills 技能系统:SKILL.md(YAML frontmatter + markdown)解析、工具白名单、技能自动匹配与激活
- Cron 定时任务持久化到 JsonKV,重启恢复 + 执行历史
- LLM 插件意图匹配器(关键词提取 + 命中)
- 工具结果智能压缩(>4000 字符摘要)

### Phase 2:多适配器增强
- `BaseAdapter` + `AdapterRegistry` 抽象层(参考 NoneBot2 Driver/Adapter)
- OneBot V11:反向 WebSocket(服务端,多连接)、正向 WebSocket(客户端,自动重连)、HTTP 上报 + HTTP API 三种模式
- QQ 官方开放平台 Webhook 适配器
- 跨平台消息段 `UniMessage`(Text/Image/At/...降级策略)

### Phase 3:记忆系统升级
- Markdown 文件记忆(`MEMORY.md`/`USER.md`/`SOUL.md`)
- SQLite + FTS5 跨会话全文搜索(参考 Hermes)
- 用户画像自动提炼 + 周期性自我反思(每 8 条消息)

### Phase 4:Agent 引擎增强
- Plan 模式(只读工具 + `PLAN.md`)与子代理委派(Explore/Plan/General)
- Todo 任务追踪
- 会话检查点/恢复(`session_save`/`session_load`/`session_list`)
- `ask_user` 多轮续接:待答问题持久化,用户回复后自动续接任务

### Phase 5:MCP 支持
- MCP Client 管理(stdin/stdout 子进程启动/停止)
- 工具自动发现与注册(`mcp:<server>_<tool>` 前缀)

### Phase 6:安全加固
- 可信目录(`trusted_folders`)+ Docker 沙箱执行(参考 Codex/Gemini CLI)
- 私聊配对审批流(参考 OpenClaw pairing,`/approve`)
- 审计日志(`data/audit.jsonl`,记录工具调用与权限决策)

### Phase 7:插件生态 + 多平台
- 外部插件自动加载/热重载(`data/plugins/*.py` + `setup(registry)` 入口)
- Telegram 适配器(长轮询)
- WebUI 增强:状态/工具/任务/广播

### Phase 8:质量与增强
- 12 项工具集成测试
- `ask_user` 续接 / 任务持久化 / 翻译工具
- WebUI 广播、MCP/技能/审计/子代理 Tabs
