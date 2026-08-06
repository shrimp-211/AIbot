# 🤖 QQ AI Agent

基于 **OneBot v11** 协议的万能 QQ AI Agent,融合 Claude Code(ReAct 工具循环、权限模型、子代理)、Codex/Gemini CLI(沙箱、MCP)、NoneBot2(适配器、依赖注入、插件生态)、AstrBot(消息管道、人格系统)的设计思想。

让 QQ 群聊/私聊用户通过自然语言驱动 Agent 完成真实任务:**联网搜索、网页抓取、读写文件、执行命令、管理 QQ 群、定时提醒、知识库检索、翻译、多轮复杂任务**等。支持多平台接入(OneBot 三模式、QQ 官方、Telegram),并拥有完整的插件系统与 WebUI 管理控制台。

## 功能特性

| 领域 | 能力 |
|---|---|
| 对话 | ReAct 工具循环(Think→Act→Observe),支持**并行工具调用**,上下文自动压缩 |
| 网络 | 多引擎搜索(Tavily→Brave→DuckDuckGo 回退)、网页抓取(SSRF 防护) |
| 文件 | 读/写/**精确编辑**(file_edit)、glob 匹配、grep 搜索(ReDoS 防护) |
| 系统 | Shell 命令执行(危险命令拦截)、定时提醒、用户询问(ask_user 多轮续接) |
| 任务 | Todo 追踪、子代理委派(Explore/Plan/General)、Plan 模式 |
| 知识 | RAG 知识库添加与检索、SQLite+FTS5 跨会话全文搜索、Markdown 文件记忆 |
| 记忆 | 三层记忆(工作+短期+长期)+ 用户画像 + 自动记忆 + 自我反思 |
| QQ 群 | 踢人/禁言/设管理/精华/公告/群文件、通知事件(欢迎/欢送/防撤回/请求审批) |
| QQ 消息 | 发图/发语音/撤回/点赞 |
| QQ 信息 | 群信息/群列表/好友列表/用户信息 |
| MCP | Model Context Protocol 外部工具接入 |
| 技能 | SKILL.md 技能系统(工具白名单 + 自动匹配) |
| 插件 | 外部插件热加载、4 种 handler、依赖注入(含 AdapterRegistry)、plugin.json 元数据 |
| 安全 | 7 级权限 + deny→ask→allow 三层决策 + 可信目录/沙箱 + 审计日志 |
| 平台 | ReverseDriver 共享服务端 + 适配器插件化(`data/adapters/`)、OneBot(反向WS/正向WS/HTTP)、QQ 官方 Webhook、Telegram |
| 管理 | WebUI 控制台(状态/工具/任务/广播/MCP/技能/审计/子代理) |

## 快速开始

### 1. 安装依赖

```bash
pip install aiohttp httpx loguru pyyaml openai
```

可选(Anthropic 模型):`pip install anthropic`

### 2. 配置

编辑 `src/config.yaml`:

- `driver.port` — 反向驱动端口(默认 6199,WS/HTTP 适配器共享),与 OneBot 客户端保持一致
- `onebot.path` — WebSocket 路径(默认 `/ws`);`onebot_http.path`/`qq_official.path` 为同端口下的子路径
- `llm.provider` — 设置模型与 API Key(建议环境变量 `${LLM_API_KEY:-}`)
- `security.super_admin_users` — 改为你自己的 QQ 号
- `webui.password` — 管理密码
- `notice.*` — 群欢迎/欢送/防撤回/请求审批策略
- `mcp.servers` — MCP 外部工具服务器

### 3. 启动

```bash
python -m src.main
```

启动后:
- OneBot WS 服务监听 `ws://127.0.0.1:6199/ws`
- WebUI 控制台位于 `http://127.0.0.1:8080`

### 4. 接入 OneBot 客户端

使用 [NapCat](https://github.com/NapNeko/NapCatQQ)、[Lagrange](https://github.com/LagrangeDev/Lagrange.Core) 或 go-cqhttp,配置反向 WebSocket 地址 `ws://127.0.0.1:6199/ws`,Access Token 与 `config.yaml` 一致。

也支持正向 WebSocket、HTTP 上报模式(`onebot_forward`/`onebot_http` 配置段)。

### 5. 使用

在群里 **@机器人** 或私聊发送需求:

> @机器人 帮我搜一下最近 AI 新闻
>
> 每2小时提醒我喝水
>
> 把这份内容保存到 notes.txt 然后翻译成英文
>
> /天气 北京
>
> /签到

## 插件系统

插件是扩展机器人功能的首选方式。外部插件放在 `data/plugins/`(内置示例随仓库),启动时自动加载,`/plugin reload` 可热重载。

### 内置插件

| 插件 | 命令 | 说明 |
|---|---|---|
| 每日签到 | `/签到` `/我的积分` `/签到排行` | 积分 + 连续签到 + 排行(持久化) |
| 掷骰子/抽签 | `/roll 2d6+3` `/抽签` | 随机娱乐 |
| 一言 | `/一言` | 在线佳句 + 本地兜底 |
| 天气查询 | `/天气 北京` | wttr.in 免费接口 |
| 关键词回复 | `/关键词` | 管理员配置关键词自动回复 |
| 翻译 | `/翻译 <文本>` | Google 免费接口,自动识别语种 |
| 群管理 | `/禁言 @成员` `/踢人 @成员` 等 | 禁言/解禁/踢人/设管理/全体禁言(管理员) |
| 复读机 | (自动) | 群内同一消息连续 3 次自动复读 |
| 计算器 | `/计算 1+2*3` | ast 白名单安全求值 |
| 诗词 | `/诗词` | 今日诗词 API + 本地兜底 |
| 示例插件 | `/plugin_echo` `/uptime` | 插件开发参考 |

### 编写插件

```python
# data/plugins/my_plugin.py
from src.adapter.event import AgentEvent
from src.storage.db import JsonKV


def setup(registry) -> None:
    @registry.command("hello")
    async def hello(event: AgentEvent, db: JsonKV):
        """依赖注入:db 为已注册的 JsonKV。"""
        await event.reply(f"Hello, {event.sender_name}!")
        return None
```

可选 `plugin.json` 元数据:

```json
{
  "name": "示例插件",
  "version": "1.0.0",
  "author": "you",
  "description": "插件说明",
  "commands": ["hello"],
  "dependencies": ["JsonKV"]
}
```

支持 4 种 handler:`@registry.command` / `@registry.message` / `@registry.regex` / `@registry.llm`,以及多轮会话控制与依赖注入(`Config`/`AuthManager`/`MemoryStore`/`JsonKV`/`AdapterRegistry`)。插件参数声明 `registry: AdapterRegistry` 即可调用平台 API(如群管插件的禁言/踢人)。

## 命令

| 命令 | 权限 | 说明 |
|---|---|---|
| `/help` `帮助` | 所有人 | 列出全部工具 |
| `/status` `状态` | 所有人 | 运行状态 |
| `/whoami` | 所有人 | 查看用户信息 |
| `/approve <码>` | 超级管理员 | 批准私聊配对 |
| `/plugins` | 所有人 | 列出外部插件及元数据 |
| `/plugin reload` | 超级管理员 | 热重载全部插件 |

## 架构

```
OneBot v11 客户端 (NapCat/Lagrange) ──┐
Telegram / QQ官方 Webhook ─────────────┤
                                       ▼
driver/    ReverseDriver 共享服务端(单端口承载 WS/HTTP 路由)
adapter/   BaseAdapter → AgentEvent(平台无关事件) → AdapterRegistry 分发
           适配器插件化:data/adapters/*.py(register 入口,新增平台不改 main.py)
                                       ▼
pipeline/  10 阶段洋葱模型
  Notice → RateLimit → ContentSafety → Security → Plugin → WakeCheck
         → PreProcess → Process → Decorate → Respond
  (Plugin 在唤醒检测之前:插件可见全部消息,命令无需 @)
                                       ▼
agent/     ReAct 循环 (并行工具调用 / 上下文压缩)
  工具(40) / 三层记忆 / 人格 / 技能 / 定时任务 / 子代理 / Plan / MCP / 钩子
                                       ▼
providers/ OpenAI兼容 + Anthropic 统一接口
```

## 目录结构

```
src/
├── main.py            # 入口(初始化顺序:基础设施→工具→Provider→引擎→管道→适配器→Cron/WebUI)
├── config.yaml        # 全局配置
├── adapter/           # 多平台适配器 + 事件/消息模型
├── pipeline/          # 消息管道(8 阶段洋葱模型)
├── agent/             # 引擎/工具/记忆/人格/技能/定时/子代理/MCP/钩子
├── providers/         # LLM Provider
├── plugins/           # 插件注册/依赖注入/会话控制
├── security/          # 权限 + SSRF 防护 + 审计
├── storage/           # JSON 键值持久化(原子写盘)
├── skills/            # 内置技能(SKILL.md)
├── data/plugins/      # 内置外部插件示例
├── data/adapters/     # 内置适配器插件(平台接入,register 入口)
├── webui/             # 管理控制台
└── utils/             # 配置/日志
```

## 测试

```bash
# 导入自检
python -c "import src.main"

# 工具集成测试(12 项)
python -m src.tests.tools_integration_test
```

## 许可证

[MIT](LICENSE)

> 设计参考:AstrBot / NoneBot2 / Claude Code / Codex / Gemini CLI / OpenCode / OpenClaw / Hermes。
