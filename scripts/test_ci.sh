#!/usr/bin/env bash
# QQ AI Agent CI 自检(复现 GitHub Actions: 安装 + 导入 + 三套测试)
set -e
cd "$(dirname "$0")"

echo "============================================"
echo "  QQ AI Agent - CI 自检"
echo "============================================"

if [ ! -d .venv ]; then
  echo "[1/4] 创建虚拟环境..."
  python3 -m venv .venv
fi
source .venv/bin/activate

echo "[2/4] 安装依赖(pip install -e .)..."
python -m pip install --upgrade pip -q
pip install -e . -q

echo "[3/4] 导入自检..."
python -u -c "import src.main"

echo "[4/4] 运行三套测试..."
python -u -m src.tests.core_tests
python -u -m src.tests.tools_integration_test
python -u -m src.tests.plugin_load_test

echo "=== CI 全部通过 (core/tools/plugin) ==="
