#!/usr/bin/env bash
# 为 FastMCP 配置 uv 测试环境：安装 uv（若无）、同步依赖、可选运行示例测试
set -e
cd "$(dirname "$0")/.."
REPO_ROOT="$(pwd)"

# 确保 uv 在 PATH 中（官方安装器会装到 ~/.local/bin）
if ! command -v uv &>/dev/null; then
  echo "正在安装 uv..."
  if command -v pip &>/dev/null; then
    pip install uv -q
  else
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="${HOME}/.local/bin:${PATH}"
  fi
fi

echo "uv 版本: $(uv --version)"
echo "项目路径: $REPO_ROOT"
echo "正在同步依赖（含 dev）..."
uv sync
echo ""
echo "环境已就绪。可执行："
echo "  运行示例 server:  uv run fastmcp run examples/config_driven_command_tools/server.py"
echo "  跑示例测试:       uv run pytest tests/examples/test_config_driven_command_tools.py -v"
echo "  跑全部测试:       uv run pytest -n auto"
echo "  Ruff 检查:        uv run ruff check ."
