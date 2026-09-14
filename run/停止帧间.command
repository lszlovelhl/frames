#!/bin/bash
# 双击本文件即可停止「帧间」（macOS）
# 首次双击若提示"无法打开"，请在「系统设置 → 隐私与安全性」允许，
# 或右键 → 打开；也可在终端执行：chmod +x "run/停止帧间.command"

RUN_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$RUN_DIR" || exit 1

echo "正在停止「帧间」…"
bash "$RUN_DIR/stop"
STATUS=$?

echo
if [ "$STATUS" -eq 0 ]; then
  echo "已停止，可以关闭本窗口。"
else
  echo "停止未完全成功（退出码 $STATUS），请把上方输出反馈给开发者。"
fi
echo "—— 按回车键关闭本窗口 ——"
read -r _ || true
exit "$STATUS"
