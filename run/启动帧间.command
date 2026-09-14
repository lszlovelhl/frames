#!/bin/bash
# 双击本文件即可启动「帧间」（macOS）
# 首次双击若提示"无法打开"，请在「系统设置 → 隐私与安全性」允许，
# 或右键 → 打开；也可在终端执行：chmod +x "run/启动帧间.command"
# 需要自定义端口时，请在终端使用：run/start --port 8080

RUN_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$RUN_DIR" || exit 1

echo "正在启动「帧间」…"
bash "$RUN_DIR/start" --open-browser
STATUS=$?

echo
if [ "$STATUS" -eq 0 ]; then
  echo "「帧间」已在后台运行，可直接关闭本窗口（服务不会退出）。"
else
  echo "启动未成功（退出码 $STATUS），请把上方输出反馈给开发者。"
fi
echo "—— 按回车键关闭本窗口 ——"
read -r _ || true
exit "$STATUS"
