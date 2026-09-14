#!/usr/bin/env bash
# frames 脚本版 sidecar 准备骨架 — backend 侧（Mac 优先，Win 提示见文档 §5.4）
# 用法：backend/scripts/prepare_sidecars.sh
# 职责：检查 ffmpeg/ffprobe/yt-dlp 是否在 PATH 或 backend/data/sidecars 下；
#       缺失时打印平台下载指引；输出检测结果（供后续 sidecar 下载器细化）。
set -euo pipefail

BACKEND_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SIDECAR_DIR="$BACKEND_DIR/data/sidecars"

mkdir -p "$SIDECAR_DIR"

echo "== frames sidecar 预检 =="
echo "sidecar 目录: $SIDECAR_DIR"

# backend/.venv 内的可执行（yt-dlp 等 pip 安装的命令行入口）
VENV_BIN="$BACKEND_DIR/.venv/bin"
[ -d "$VENV_BIN" ] || VENV_BIN="$BACKEND_DIR/.venv/Scripts"

# 1) 先查 PATH，再查 sidecar 目录内可执行文件，最后查 backend/.venv
check_bin() {
  local name="$1"
  local found=""
  if command -v "$name" >/dev/null 2>&1; then
    found="$(command -v "$name")"
  else
    # sidecars/ffmpeg/ffmpeg、sidecars/ffmpeg/ffprobe、sidecars/yt-dlp/yt-dlp 等约定
    local candidates
    candidates="$(find "$SIDECAR_DIR" -type f -name "$name" -perm -u+x 2>/dev/null | head -1 || true)"
    [ -n "$candidates" ] && found="$candidates"
    if [ -z "$found" ] && [ -x "$VENV_BIN/$name" ]; then
      found="$VENV_BIN/$name"
    fi
  fi
  if [ -n "$found" ]; then
    local ver=""
    case "$name" in
      ffmpeg|ffprobe) ver="$("$found" -version 2>/dev/null | head -1 | sed -E 's/.*version ([^ ]+).*/\1/')" ;;
      *) ver="$("$found" --version 2>/dev/null | head -1)" ;;
    esac
    if [ -n "$ver" ]; then
      echo "OK   $name -> $found ($ver)"
    else
      echo "OK   $name -> $found"
    fi
    return 0
  fi
  echo "MISS $name"
  return 1
}

MISS_ANY=0

echo ""
echo "-- 二进制检测 --"
if ! check_bin ffmpeg; then MISS_ANY=1; fi
if ! check_bin ffprobe; then MISS_ANY=1; fi
if ! check_bin yt-dlp; then MISS_ANY=1; fi

# 2) 缺失提示（Mac 优先；Win 文案见 docs §5.4）
if [ "$MISS_ANY" = "1" ]; then
  echo ""
  echo "以下任选其一补齐："
  echo "  [Mac] 推荐 brew：brew install ffmpeg yt-dlp"
  echo "  [Mac] 静态包：ffmpeg 见 evermeet.cx / ffmpeg.org 链接；yt-dlp 见 https://github.com/yt-dlp/yt-dlp/releases"
  echo "  [Win] ffmpeg: https://www.gyan.dev/ffmpeg/builds/ 或 BtbN Releases；yt-dlp.exe: https://github.com/yt-dlp/yt-dlp/releases"
  echo "  或将可执行文件放入 $SIDECAR_DIR/{ffmpeg,yt-dlp}/ 目录即可被识别。"
fi

# 3) faster-whisper 模型路径说明
# 未在当前 shell 设置时，回退读 backend/.env（不覆盖已有环境变量，不加载其它键）
if [ -z "${WHISPER_MODEL_PATH:-}" ] && [ -f "$BACKEND_DIR/.env" ]; then
  WHISPER_MODEL_PATH="$(grep -E '^WHISPER_MODEL_PATH=' "$BACKEND_DIR/.env" | tail -1 | cut -d= -f2- | tr -d '"' || true)"
fi
if [ -z "${WHISPER_MODEL:-}" ] && [ -f "$BACKEND_DIR/.env" ]; then
  WHISPER_MODEL="$(grep -E '^WHISPER_MODEL=' "$BACKEND_DIR/.env" | tail -1 | cut -d= -f2- | tr -d '"' || true)"
fi

echo ""
echo "-- faster-whisper 模型 --"
echo "默认缓存: ~/.cache/huggingface（当前约 179MB）"
echo "后端 transcribe.py 支持 WHISPER_MODEL_PATH 指向本地 ctranslate2 模型目录"
echo "打包外置建议: export WHISPER_MODEL_PATH=<user_data>/sidecars/models/whisper-medium-ct2"
echo "当前 WHISPER_MODEL_PATH=${WHISPER_MODEL_PATH:-<未设置，使用默认>}"
echo "当前 WHISPER_MODEL=${WHISPER_MODEL:-<未设置，默认 medium>}"

echo ""
if [ "$MISS_ANY" = "0" ]; then
  echo "== 预检完成：全部就绪 =="
else
  echo "== 预检完成：存在缺失项（不影响 dev 启动；影响采集/转写等素材链路）=="
  exit 1
fi
