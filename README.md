# 🤖 QQ AI Agent

基于 **OneBot v11** 协议的万能 QQ AI Agent。融合 Claude Code(ReAct 工具循环、三层权限)、NoneBot2(适配器、依赖注入、会话控制)、AstrBot(消息管道、人格系统)的设计思想。

让 QQ 群聊/私聊用户通过自然语言驱动 Agent 完成真实任务:**联网搜索、网页抓取、读写文件、执行命令、管理 QQ 群、定时提醒、知识库检索**等。

## 功能特性

| 领域 | 能力 |
|---|---|
| 对话 | ReAct 工具循环,LLM 自主调用工具完成任务 |
| 网络 | 多引擎搜索(Tavily→Brave→DuckDuckGo 回退)、网页抓取(SSRF 防护) |
| 文件 | 读写文件、glob 匹配、grep 搜索(ReDoS 防护) |
| 系统 | Shell 命令执行(危险命令拦截)、定时提醒(自然语言时间)、用户询问 |
| 任务 | 多步骤任务状态跟踪 |
| 知识 | RAG 知识库添加与检索(JSON 持久化) |
| QQ 群 | 踢人/禁言/设管理/精华/公告/群文件 |
| QQ 消息 | 发图/发语音/撤回/点赞 |
| QQ 信息 | 群信息/群列表/好友列表/用户信息 |
| 记忆 | 三层记忆(工作+短期+长期)+ 自动记忆 |
| 安全 | 7 级权限 + deny→ask→allow 三层决策 + 注入防护 |
| 插件 | 命令/消息/正则 handler + 多轮会话控制 + 依赖注入 |
| 管理 | WebUI 控制台(状态/工具/任务/实时聊天) |

## 快速开始

### 1. 安装依赖

```bash
pip install aiohttp httpx loguru pyyaml openai
```

可选(Anthropic 模型):`pip install anthropic`

### 2. 配置

编辑 `src/config.yaml`:

- `onebot.port` — WebSocket 监听端口,保持与 OneBot 客户端一致
- `llm.provider` — 设置模型与 API Key(可用环境变量 `${LLM_API_KEY:-}`)
- `security.super_admin_users` — 改为你自己的 QQ 号
- `webui.password` — 管理密码

### 3. 启动

```bash
python -m src.main
```

启动后:
- OneBot WS 服务监听 `ws://127.0.0.1:6199/ws`
- WebUI 控制台位于 `http://127.0.0.1:8080`

### 4. 接入 OneBot 客户端

使用 [NapCat](https://github.com/NapNeko/NapCatQQ)、[Lagrange](https://github.com/LagrangeDev/Lagrange.Core) 或 go-cqhttp,配置:
- 反向 WebSocket 地址: `ws://127.0.0.1:6199/ws`
- Access Token 与 `config.yaml` 一致

### 5. 使用

在群里 **@机器人** 或私聊发送需求,例如:

> @机器人 帮我搜一下最近 AI 新闻
>
> 每2小时提醒我喝水
>
> 把这份内容保存到 notes.txt

## 架构

```
OneBot v11 客户端 (NapCat/Lagrange)
        │  全双工 WebSocket
        ▼
adapter/   OneBotV11Adapter → AgentEvent(平台无关事件)
        ▼
pipeline/  7 阶段洋葱模型
  WakeCheck → RateLimit → ContentSafety → PreProcess → Process → Decorate → Respond
        ▼
agent/     ReAct 循环 (Think→Act→Observe)
  工具(26) / 三层记忆 / 人格 / 定时任务 / 钩子 / 上下文压缩
        ▼
providers/ OpenAI兼容 + Anthropic 统一接口
```

## 目录结构

```
src/
├── main.py            # 入口
├── config.yaml        # 全局配置
├── adapter/           # OneBot v11 适配器 + 事件/消息模型
├── pipeline/          # 消息管道(7阶段)
├── agent/             # 引擎/工具/记忆/人格/定时/钩子
│   ├── engine.py      # ReAct 循环
│   ├── tools/         # 26 个工具
│   ├── memory/        # 三层记忆
│   ├── persona.py     # 多人格
│   ├── proactive.py   # 定时任务
│   ├── compressor.py  # 上下文压缩
│   └── hooks.py       # 生命周期钩子
├── providers/         # LLM Provider
├── plugins/           # 插件注册/依赖注入/会话控制
├── security/          # 权限 + 安全
├── storage/           # JSON 持久化
├── webui/             # 管理控制台
└── utils/             # 配置/日志
```

## 许可证

[MIT](LICENSE)

> 参考项目 AstrBot / NoneBot2 / Open-ClaudeCode 源码及设计文档见 `reference/`(不随本仓库分发)。
