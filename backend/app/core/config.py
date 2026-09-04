"""帧间后端配置 — 读取 backend/.env
梁龙科技 · 帧间 Frames v2
"""
import os
from pathlib import Path

from dotenv import load_dotenv

# backend 根目录（config.py 位于 backend/app/core/）
BACKEND_DIR = Path(__file__).resolve().parents[2]
load_dotenv(BACKEND_DIR / ".env")

# --- 数据库 ---
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://zhuolittlelong@localhost:5432/frames_dev",
)

# --- 本地媒体缓存（视频/音频/抽帧/BGM） ---
MEDIA_DIR = BACKEND_DIR / "data" / "media"

# --- DeepSeek 模型网关 ---
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_MODEL_FLASH = os.getenv("DEEPSEEK_MODEL_FLASH", "deepseek-v4-flash")
DEEPSEEK_MODEL_PRO = os.getenv("DEEPSEEK_MODEL_PRO", "deepseek-v4-pro")
DEEPSEEK_MODEL_VISION = os.getenv("DEEPSEEK_MODEL_VISION", "deepseek-v4-flash-vision-exp")
