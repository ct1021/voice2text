#!/bin/bash
# Voice2Text 启动脚本 (macOS / Linux)
cd "$(dirname "$0")"
nohup ./.venv/bin/python voice2text.py > voice2text.log 2>&1 &
echo "voice2text 已启动 — 桌面会出现悬浮球。"
echo "停止：右键悬浮球退出，或运行  pkill -f voice2text.py"
