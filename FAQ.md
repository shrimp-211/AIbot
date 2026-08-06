# QQ AI Agent 常见问题(FAQ)

> 启动/连接/使用过程中遇到问题,先看这里。仍无法解决可到 [Issues](https://github.com/shrimp-211/AIbot/issues) 反馈(附上 `data/logs/` 下的日志与版本号)。

## 目录

- [启动与配置](#启动与配置)
- [OneBot 客户端连接不上](#onebot-客户端连接不上)
- [模型报错](#模型报错)
- [群聊无响应 / 机器人不回复](#群聊无响应--机器人不回复)
- [WebUI 打不开](#webui-打不开)
- [QQ 官方 Webhook 事件全被拒绝](#qq-官方-webhook-事件全被拒绝)
- [Docker 部署问题](#docker-部署问题)
- [权限与安全警告](#权限与安全警告)
- [日志与诊断](#日志与诊断)

---

## 启动与配置

### 启动报 `ModuleNotFoundError: No module named 'src'`?

没有安装依赖包。项目根目录(含 `pyproject.toml`)执行:

```bash
pip install -e .
```

该命令把当前目录注册为 `src` 包,之后可在**任意目录**运行 `python -m src.main`。

### 第一次启动后机器人没有回复?

先确认三件事:
1. `.env` 中 `LLM_API_KEY` 已填写(复制 `.env.example` 为 `.env` 并修改)。
2. `config.yaml` 中 `security.super_admin_users` 已改成你自己的 QQ 号(启动日志会警告占位符 `123456789`)。
3. 日志中 WebSocket/WebUI 监听是否正常(见 [日志与诊断](#日志与诊断))。

### 提示 `⚠️ 安全警告:trusted_folders 为空`?

这是提示,不是错误。机器人会正常工作,但 shell/文件工具可操作任意路径,对外部署有风险。建议把 `config.yaml` 的 `agent.workdir` 设为固定目录,并在 `security.trusted_folders` 里只允许该目录:

```yaml
agent:
  workdir: "/data/work"
security:
  trusted_folders: ["/data/work"]
  sandbox_enabled: true   # 更安全:命令经 Docker 沙箱执行
```

---

## OneBot 客户端连接不上

### 反向 WebSocket 连不上(`ws://127.0.0.1:6199/ws`)

按顺序排查:

| 检查项 | 说明 |
|---|---|
| 端口被占用 | 换端口,或 `netstat -ano \| findstr 6199` 查看占用进程 |
| 路径不一致 | 客户端配置的路径必须与 `config.yaml` 的 `onebot.path`(默认 `/ws`)完全一致 |
| Access Token 不一致 | 客户端填的 token 必须与 `onebot.token` 相同;两边都留空也可以 |
| 防火墙/跨机器 | 客户端不在本机时,`onebot.host` 需改为 `0.0.0.0`,并放行防火墙端口 |
| self_id 未设置 | 若 @唤醒不生效,在配置中填 `onebot.self_id`(机器人 QQ 号) |

客户端配置示例(NapCat/Lagrange 的反向 WS):

```
地址:    ws://127.0.0.1:6199/ws
Token:   (与 config.yaml 一致或留空)
```

### 我的机器人跑在 Docker/内网,无法被反向连入?

用**正向 WebSocket** 模式,机器人主动连 OneBot 实现端:

```yaml
onebot_forward:
  enabled: true
  url: ws://127.0.0.1:6700   # OneBot 实现端的 WS 服务地址
```

### 用 HTTP 上报模式?

启用 `onebot_http` 段,OneBot 客户端把事件 POST 到 `http://机器人:6198/onebot`,同时配置 `http_url` 指向 OneBot 的 HTTP API 地址用于主动调用。

---

## 模型报错

### `401 Unauthorized` / `AuthenticationError`

API Key 无效。检查 `.env` 的 `LLM_API_KEY`,确认没有多余空格;OpenAI 兼容服务还需要在 `config.yaml` 设置 `llm.provider.base_url`(如 DeepSeek 是 `https://api.deepseek.com/v1`)。

### `404` / `Model not found`

模型名错误,或该模型在你的服务商处不存在。用 `/model <模型名>`(超级管理员)临时切换验证,或改 `config.yaml` 的 `llm.provider.model`。

### `429 Too Many Requests` / 限流

触发限流。`config.yaml` 的 `llm.provider.retry` 已内置指数退避自动重试;仍频繁触发请降低使用频率,或换更高配额度。

### 上下文超长被截断?

`agent.max_context_tokens` 默认 `0` = 按模型自动推断窗口,超过 82% 自动压缩。若你的模型上下文窗口推断不准,显式配置:

```yaml
llm:
  provider:
    context_window: 128000
```

### 想要多模型自动容灾?

配置 `fallback_providers`(结构同主 provider),主 provider 失败会自动切换,冷却后自动重试:

```yaml
llm:
  provider:
    type: openai_compatible
    model: deepseek-chat
    base_url: https://api.deepseek.com/v1
    api_key: ${LLM_API_KEY:-}
    fallback_providers:
      - type: anthropic
        model: claude-sonnet-4-6
        api_key: ${ANTHROPIC_API_KEY:-}
```

---

## 群聊无响应 / 机器人不回复

按优先级检查:

1. **没 @ 也没唤醒词**:群聊默认需要 **@机器人** 或命中唤醒词(`pipeline.wake_words`,默认 `机器人`/`小助手`/`AI`)才会响应。私聊则直接对话即可。
2. **群白名单**:`pipeline.group_whitelist` 非空时,只响应列表内的群(QQ 群号)。
3. **消息太长**:`pipeline.content_safety.max_length`(默认 5000)超长被拦截。
4. **限流**:`pipeline.rate_limit` 默认 30 条/分钟,刷屏会被限流。
5. **回复被抑制**:群聊中为了避免刷屏,进度提示(⏳/🔧)只出现在私聊和 WebUI,群聊直接输出最终结果。若完全无回复,看日志确认是否走到了 LLM 调用(见 [日志与诊断](#日志与诊断))。

### 如何确认机器人真的收到了消息?

临时把 `config.yaml` 的 `pipeline.group_whitelist` 留空、`security.pairing_enabled` 设为 `false`,重启后私聊机器人发送任意内容,观察日志是否出现 `收到消息: user_id=... text=...`。

---

## WebUI 打不开

| 现象 | 处理 |
|---|---|
| `127.0.0.1:8080` 无响应 | 确认 `webui.enabled: true` 且端口未被占用;容器部署时检查 `-p 8080:8080` |
| 输入密码不对 | 密码留空时首次启动会**随机生成并打印在日志**;找不到就删掉 `data/webui.json` 重启重新生成 |
| 远程访问不了 | `webui.host` 改为 `0.0.0.0`,并设置固定密码(`webui.password`)后放行防火墙 |

---

## QQ 官方 Webhook 事件全被拒绝

`config.yaml` 的 `qq_official` 段**必须配置 `sign_secret`**,否则验签 fail-closed,所有上报都被拒。

```yaml
qq_official:
  enabled: true
  app_id: "你的 AppID"
  app_secret: "你的 AppSecret"
  sign_secret: "你在开放平台配置的回调签名密钥"
  path: /qq-official
```

回调地址填:`https://你的域名:6197/qq-official`。若在 QQ 开放平台填的是 `https://` 公网地址,本地需要反代(如 nginx)把公网端口转发到 `127.0.0.1:6197`。

---

## Docker 部署问题

### 构建/启动命令

```bash
docker compose up -d
```

数据持久化在 `./data`(`agent.json` / `auth.json` / `memory.sqlite3` / `audit.jsonl`)。

### 容器内模型/权限要改?

两个办法:
- 宿主机写 `config.local.yaml` 覆盖(docker-compose 已挂载 `./config.local.yaml:/app/config.local.yaml:ro`,文件不存在则忽略)。
- 改完后 `docker compose restart`。

### `.env` 在宿主机没生效?

docker-compose 从**宿主机环境变量**或**同目录 `.env`** 读取 `LLM_API_KEY` 等。确认 `.env` 与 `docker-compose.yml` 在同一目录,再 `docker compose up -d`(compose 启动时才读取,改后需重建 `docker compose up -d`)。

### 容器内无法连接宿主机上的 OneBot 客户端?

容器是隔离网络。反向模式:`onebot.host` 设 `0.0.0.0`,`ports` 把 `6199:6199` 映射出去,客户端连**宿主机 IP** 的 `6199`。或者改用 `onebot_forward` 正向模式,`url` 填宿主机局域网 IP。

### 容器日志怎么看?

```bash
docker logs -f qq-ai-agent
```

---

## 权限与安全警告

### 什么是 `super_admin_users` 占位符警告?

默认值是 `["123456789"]`(占位符)。未修改时启动会警告,且以该占位号做鉴权。务必改成你自己的 QQ 号。

### 危险命令被拦截 / curl 需要管理员?

内置安全规则:**禁读 `.env`/`secrets`、禁危险命令(rm -rf / 等)、`curl`/`wget`/`sudo` 需管理员权限**。这是设计行为,非 bug。管理员名单在 `security.admin_users`。

### 私聊需要 `/approve` 批准?

`security.pairing_enabled: true` 时,未配对用户的私聊请求进入审批队列,超级管理员用 `/approve <配对码>` 批准(配对码在机器人回复里给出)。不需要此功能就设为 `false`。

### 审计日志在哪?

`security.audit_enabled: true` 时,所有工具调用与权限决策记录到 `data/audit.jsonl`,可在 WebUI「审计」页查看。

---

## 日志与诊断

| 位置 | 内容 |
|---|---|
| `data/logs/` | 运行日志(按天分文件,含完整 traceback) |
| `data/audit.jsonl` | 工具调用与权限决策审计 |
| 控制台 | 启动时打印监听地址、WebUI 密码(首次)、安全警告 |

### 提交 Issue 前请附带

1. 版本号(仓库 commit 或 `docker images` 的 IMAGE ID)。
2. 复现步骤与触发消息。
3. `data/logs/` 当天日志的**关键片段**(脱敏,不要贴完整日志或密钥)。
