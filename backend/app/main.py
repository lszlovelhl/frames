"""帧间 (Frames) API 入口 — v2 骨架
梁龙科技 · viral_analyzer 重写
"""

import os

from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

app = FastAPI(title="帧间 Frames API", version="0.1.0")

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://zhuolittlelong@localhost:5432/frames_dev",
)

engine = create_async_engine(DATABASE_URL)


@app.get("/")
async def root():
    return {"app": "frames", "version": "0.1.0", "status": "ok"}


@app.get("/api/health/db")
async def db_health():
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
    return {"database": "ok"}
