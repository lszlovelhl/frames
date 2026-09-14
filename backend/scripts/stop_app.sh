#!/usr/bin/env bash
# frames 脚本版（解压即跑）Mac 停止脚本 — backend 侧骨架
# 用法：backend/scripts/stop_app.sh
# 行为：读 data/uvicorn.pid → SIGTERM 优雅停止 → 兜底按端口/命令查杀 → 清理 PID 文件。
set -euo pipefail

BACKEND_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATA_DIR="$BACKEND_DIR/data"
PID_FILE="$DATA_DIR/uvicorn.pid"
PORT="${PORT:-8000}"

stop_by_pid_file() {
  if [ ! -f "$PID_FILE" ]; then
    echo "[stop_app] 无 PID 文件"
    return 1
  fi
  PID="$(cat "$PID_FILE" 2>/dev/null || true)"
  if [ -z "$PID" ] || ! kill -0 "$PID" 2>/dev/null; then
    echo "[stop_app] PID=$PID 不存在或已退出，清理 PID 文件"
    rm -f "$PID_FILE"
    return 1
  fi
  # 端口校验：PID 文件进程未监听目标端口时视为不匹配，转端口兜底
  if ! lsof -tiTCP:"$PORT" -sTCP:LISTEN 2>/dev/null | grep -qx "$PID"; then
    echo "[stop_app] PID=$PID 未监听端口 ${PORT}，转按端口清理"
    return 1
  fi
  echo "[stop_app] 发送 SIGTERM -> PID=$PID"
  kill -TERM "$PID" 2>/dev/null || true
  for _ in $(seq 1 10); do
    kill -0 "$PID" 2>/dev/null || { echo "[stop_app] 已退出 PID=$PID"; rm -f "$PID_FILE"; return 0; }
    sleep 0.5
  done
  echo "[stop_app] 超时未退出，SIGKILL -> PID=$PID" >&2
  kill -9 "$PID" 2>/dev/null || true
  rm -f "$PID_FILE"
  return 0
}

if stop_by_pid_file; then
  exit 0
fi

# 兜底：按端口清理（非 PID 文件场景，如手动 nohup 启动的 uvicorn）
PIDS="$(lsof -tiTCP:"$PORT" -sTCP:LISTEN 2>/dev/null || true)"
if [ -n "$PIDS" ]; then
  echo "[stop_app] 端口 $PORT 占用进程: ${PIDS}，执行 SIGTERM"
  kill -TERM $PIDS 2>/dev/null || true
  sleep 2
  REMAIN="$(lsof -tiTCP:"$PORT" -sTCP:LISTEN 2>/dev/null || true)"
  if [ -n "$REMAIN" ]; then
    echo "[stop_app] 仍占用端口 $PORT: ${REMAIN}，SIGKILL" >&2
    kill -9 $REMAIN 2>/dev/null || true
  fi
  exit 0
fi
echo "[stop_app] 未发现运行中的服务（端口 $PORT 无监听）"
exit 0
