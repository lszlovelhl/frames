"""帧间 (Frames) API 入口 — v2 骨架
梁龙科技 · viral_analyzer 重写
"""

from fastapi import FastAPI
from sqlalchemy import text

from app.core.config import DATABASE_URL
from app.db import engine
from app.routers import ai as ai_router

app = FastAPI(title="帧间 Frames API", version="0.2.0")
app.include_router(ai_router.router)


@app.get("/")
async def root():
    return {"app": "frames", "version": "0.2.0", "status": "ok"}


@app.get("/api/health/db")
async def db_health():
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
    return {"database": "ok"}
