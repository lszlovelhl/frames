#!/usr/bin/env bash
# frames 脚本版（解压即跑）Mac 启动脚本 — backend 侧骨架
# 用法：backend/scripts/run_app.sh [PORT]
# 说明：cd 到 backend 后拉起 .venv/bin/python -m uvicorn，日志 data/logs/uvicorn.log
#       含 PID 记录 + 重复启动防护（见 stop_app.sh 配套停止）。
set -euo pipefail

BACKEND_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT="${1:-${PORT:-8000}}"
DATA_DIR="$BACKEND_DIR/data"
LOG_DIR="$DATA_DIR/logs"
LOG_FILE="$LOG_DIR/uvicorn.log"
PID_FILE="$DATA_DIR/uvicorn.pid"
PYTHON_BIN="$BACKEND_DIR/.venv/bin/python"

mkdir -p "$LOG_DIR"

# 重复启动防护：PID 文件有效则拒绝拉起
if [ -f "$PID_FILE" ]; then
  OLD_PID="$(cat "$PID_FILE" 2>/dev/null || true)"
  if [ -n "$OLD_PID" ] && kill -0 "$OLD_PID" 2>/dev/null; then
    echo "[run_app] 已在运行 PID=${OLD_PID}（端口 ${PORT}）。如需重启请先执行: $BACKEND_DIR/scripts/stop_app.sh" >&2
    exit 1
  fi
  echo "[run_app] 清理失效 PID 文件（旧 PID=${OLD_PID}）" >&2
  rm -f "$PID_FILE"
fi

if [ ! -x "$PYTHON_BIN" ]; then
  echo "[run_app] 缺少 ${PYTHON_BIN}，请先创建 backend/.venv（见 docs/11-local-packaging.md）" >&2
  exit 1
fi

cd "$BACKEND_DIR"
nohup "$PYTHON_BIN" -m uvicorn app.main:app --host 127.0.0.1 --port "$PORT" >> "$LOG_FILE" 2>&1 &
NEW_PID=$!
echo "$NEW_PID" > "$PID_FILE"

sleep 2
if kill -0 "$NEW_PID" 2>/dev/null; then
  echo "[run_app] 启动成功 PID=$NEW_PID 端口=$PORT"
  echo "[run_app] 日志: $LOG_FILE"
else
  echo "[run_app] 启动失败，最近日志如下：" >&2
  tail -20 "$LOG_FILE" >&2 || true
  rm -f "$PID_FILE"
  exit 1
fi
