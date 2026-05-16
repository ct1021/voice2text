#!/bin/bash
# Voice2Text 一键安装 (macOS / Linux)
set -e
cd "$(dirname "$0")"

echo "=== Voice2Text 安装 ==="

if ! command -v uv &> /dev/null; then
    echo "未找到 uv，正在安装..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
fi

echo "创建 Python 3.11 虚拟环境..."
uv venv --python 3.11

echo "安装依赖（约 1-3 分钟）..."
uv pip install -r requirements.txt

if [ ! -f glossary.txt ]; then
    cp glossary.example.txt glossary.txt
    echo "已生成 glossary.txt（可编辑加入你的常用专有名词）"
fi

echo ""
echo "=== 安装完成 ==="
echo "启动方式： ./run.sh"
echo "首次启动会自动下载语音模型（约 1.5GB）。"
echo ""
echo "[macOS 重要] 首次运行需授权辅助功能权限："
echo "  系统设置 > 隐私与安全性 > 辅助功能 > 勾选你的终端 / Python"
echo "  否则全局热键无法工作。"
