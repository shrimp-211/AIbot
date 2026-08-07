#!/usr/bin/env bash
# QQ AI Agent 一键启动 (测试环境, Linux/macOS)
set -e
cd "$(dirname "$0")"

echo "============================================"
echo "  QQ AI Agent - 一键启动 (测试环境)"
echo "============================================"

if ! command -v python3 >/dev/null 2>&1; then
  echo "[错误] 未找到 python3,请先安装 Python 3.10+"
  exit 1
fi

if [ ! -d .venv ]; then
  echo "[1/3] 创建虚拟环境..."
  python3 -m venv .venv
fi
source .venv/bin/activate

echo "[2/3] 安装依赖..."
python -m pip install --upgrade pip -q
pip install -e . -q

if [ ! -f .env ] && [ -f .env.example ]; then
  echo "[提示] 首次运行请: cp .env.example .env  然后编辑填入 LLM_API_KEY"
fi

echo "[3/3] 启动 QQ AI Agent..."
echo "  WebUI : http://127.0.0.1:8080"
echo "  OneBot: ws://127.0.0.1:6199/ws (NapCat/Lagrange 在此反向连接)"
echo "============================================"
python -m src.main
