# QQ AI Agent 一键部署镜像
# 构建: docker build -t qq-ai-agent .
# 运行: docker compose up -d(推荐)  或  docker run --rm -p 6199:6199 -p 8080:8080 qq-ai-agent
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# 仓库根即 `src` 包:editable 安装注册包名,使 `python -m src.main` 可在任意目录运行
COPY . .
RUN pip install --no-cache-dir -e .

# 运行数据持久化目录(agent.json / auth.json / memory.sqlite3 / audit.jsonl / 日志)
VOLUME /app/data

# OneBot WS(6199) 与 WebUI(8080)
EXPOSE 6199 8080

CMD ["python", "-m", "src.main"]
