"""帧间 (Frames) API 入口 — v2 骨架
梁龙科技 · viral_analyzer 重写
"""

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from app.core.config import MEDIA_DIR
from app.db import engine
from app.media import cookies
from app.routers import ai as ai_router
from app.routers import creations as creations_router
from app.routers import elements as elements_router
from app.routers import platforms as platforms_router
from app.routers import usage as usage_router
from app.routers import videos as videos_router


@asynccontextmanager
async def lifespan(_: FastAPI):
    # 登录态定期巡检（每小时标记过期登录态）
    task = asyncio.create_task(cookies.periodic_check())
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


app = FastAPI(title="帧间 Frames API", version="0.3.0", lifespan=lifespan)

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
app.include_router(platforms_router.router)
app.include_router(elements_router.router)
app.include_router(creations_router.router)

# 本地媒体静态服务：/media/<work_dir>/...
app.mount("/media", StaticFiles(directory=str(MEDIA_DIR)), name="media")


@app.get("/")
async def root():
    return {"app": "frames", "version": "0.3.0", "status": "ok"}


@app.get("/api/health/db")
async def db_health():
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
    return {"database": "ok"}
