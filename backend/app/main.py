"""帧间 (Frames) API 入口 — v2 骨架
梁龙科技 · viral_analyzer 重写
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.db import engine
from app.routers import ai as ai_router
from app.routers import videos as videos_router

app = FastAPI(title="帧间 Frames API", version="0.3.0")

# 开发期允许前端本地调试
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(ai_router.router)
app.include_router(videos_router.router)


@app.get("/")
async def root():
    return {"app": "frames", "version": "0.3.0", "status": "ok"}


@app.get("/api/health/db")
async def db_health():
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
    return {"database": "ok"}
