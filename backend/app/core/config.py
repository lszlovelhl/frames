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
# 本地单体默认 SQLite（backend/data/frames.db）；PG 连接串仅迁移/回滚时经 DATABASE_URL 环境变量覆盖
_DATA_DIR = BACKEND_DIR / "data"
_DATA_DIR.mkdir(parents=True, exist_ok=True)
SQLITE_DB_PATH = _DATA_DIR / "frames.db"
_SQLITE_URL = f"sqlite+aiosqlite:///{SQLITE_DB_PATH.as_posix()}"
DATABASE_URL = os.getenv("DATABASE_URL", _SQLITE_URL)

# 旧 PostgreSQL 连接串（迁移/回滚参考，勿作默认）
PG_LEGACY_URL = "postgresql+asyncpg://zhuolittlelong@localhost:5432/frames_dev"

# --- 本地媒体缓存（视频/音频/抽帧/BGM） ---
MEDIA_DIR = BACKEND_DIR / "data" / "media"

# --- DeepSeek 模型网关 ---
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_MODEL_FLASH = os.getenv("DEEPSEEK_MODEL_FLASH", "deepseek-v4-flash")
DEEPSEEK_MODEL_PRO = os.getenv("DEEPSEEK_MODEL_PRO", "deepseek-v4-pro")
DEEPSEEK_MODEL_VISION = os.getenv("DEEPSEEK_MODEL_VISION", "deepseek-v4-flash-vision-exp")

# --- AI 费用估算单价（元 / 百万 token，输入 / 输出） ---
# 按 DeepSeek 公开价近似维护，可在 .env 以 AI_PRICE_*_CNY_PER_M=flash:1,pro:2 覆盖；
# 展示口径一律标注「估算」，仅用于成本可视化，不代表账单。
AI_PRICE_IN_CNY_PER_M: dict[str, float] = {"flash": 1.0, "pro": 2.0, "vision": 2.0}
AI_PRICE_OUT_CNY_PER_M: dict[str, float] = {"flash": 2.0, "pro": 8.0, "vision": 8.0}


def _parse_price_env(raw: str | None, fallback: dict[str, float]) -> dict[str, float]:
    if not raw:
        return fallback
    out = dict(fallback)
    for part in raw.split(","):
        if ":" not in part:
            continue
        k, v = part.split(":", 1)
        try:
            out[k.strip()] = float(v.strip())
        except ValueError:
            continue
    return out


AI_PRICE_IN_CNY_PER_M = _parse_price_env(
    os.getenv("AI_PRICE_IN_CNY_PER_M"), AI_PRICE_IN_CNY_PER_M
)
AI_PRICE_OUT_CNY_PER_M = _parse_price_env(
    os.getenv("AI_PRICE_OUT_CNY_PER_M"), AI_PRICE_OUT_CNY_PER_M
)
