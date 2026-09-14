"""帧间 (Frames) API 入口 — v2 骨架
梁龙科技 · viral_analyzer 重写

单端口 app 式体验：存在 frontend/dist 时挂载到根路径，访问 http://127.0.0.1:<port>/
即为完整界面；dist 缺失（未构建/被裁剪）时优雅降级为仅 API（根路径返回 JSON）。
"""

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.config import MEDIA_DIR
from app.db import engine
from app.media import cookies
from app.routers import ai as ai_router
from app.routers import billing as billing_router
from app.routers import creations as creations_router
from app.routers import elements as elements_router
from app.routers import platforms as platforms_router
from app.routers import prompts as prompts_router
from app.routers import products as products_router
from app.routers import providers as providers_router
from app.routers import usage as usage_router
from app.routers import videos as videos_router


@asynccontextmanager
async def lifespan(_: FastAPI):
    # 启动横幅：单端口 app 式体验是否就绪（前端 dist 是否存在）
    if WEB_UI_ENABLED:
        logger.info("前端已托管：%s（访问 http://127.0.0.1:<port>/ 即完整界面）", FRONTEND_DIST)
    else:
        logger.warning("未找到 %s/index.html，已降级为仅 API 模式", FRONTEND_DIST)
    # 登录态定期巡检（每小时标记过期登录态）
    task = asyncio.create_task(cookies.periodic_check())
    # 互动数据定时刷新（每 6 小时真实重抓有基准的 B 站视频，供变化对比）
    from app.services.stats_scheduler import periodic_stats_refresh

    stats_task = asyncio.create_task(periodic_stats_refresh())
    # 拆解后台队列（批量提交用）：串行执行，进度落库供前端常驻展示
    from app.services import breakdown_jobs

    await breakdown_jobs.start_workers()
    # 三层分库提示词（tl1~tl6，第 8 章）幂等落库：仅当 code 内容变更才新增版本，不删历史
    try:
        from app.db import SessionLocal
        from app.services.three_layer.seed_prompts import seed_three_layer_prompts

        async with SessionLocal() as _db:
            report = await seed_three_layer_prompts(_db)
            await _db.commit()
        logger.info("三层提示词就绪：%s", [f"{r['code']}#{r['version']}:{r['action']}" for r in report])
    except Exception:  # noqa: BLE001
        logger.warning("三层提示词 seed 失败（不影响启动）", exc_info=True)
    try:
        yield
    finally:
        await breakdown_jobs.stop_workers()
        for t in (task, stats_task):
            t.cancel()
            try:
                await t
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
app.include_router(prompts_router.router)
app.include_router(products_router.router)
app.include_router(usage_router.router)
app.include_router(providers_router.router)
app.include_router(billing_router.router)

# 本地媒体静态服务：/media/<work_dir>/...
app.mount("/media", StaticFiles(directory=str(MEDIA_DIR)), name="media")

logger = logging.getLogger("app.main")

# 前端构建产物目录：默认 <项目根>/frontend/dist，可用 FRONTEND_DIST 覆盖（打包/裁剪场景）
FRONTEND_DIST = Path(
    os.getenv("FRONTEND_DIST")
    or str(Path(__file__).resolve().parents[2] / "frontend" / "dist")
)


@app.get("/api/health/db")
async def db_health():
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
    return {"database": "ok"}


# --------------------------------------------------------------------------- #
# 单端口 app 式托管（必须放在所有 API 路由之后：根路径兜底不吞掉 /api）
# --------------------------------------------------------------------------- #
class SpaStaticFiles(StaticFiles):
    """前端单页托管：未命中的路径回退 index.html（支持前端路由刷新/深链）。"""

    async def get_response(self, path: str, scope):  # type: ignore[override]
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code == 404:
                return await super().get_response("index.html", scope)
            raise


WEB_UI_ENABLED = (FRONTEND_DIST / "index.html").is_file()

if WEB_UI_ENABLED:
    # 挂在最后：/api、/media 等已注册路由优先匹配，其余交给前端
    app.mount("/", SpaStaticFiles(directory=str(FRONTEND_DIST), html=True), name="web")
else:
    # 优雅降级：无前端构建产物时，根路径返回 JSON，仅提供 API
    @app.get("/")
    async def root():
        return {
            "app": "frames",
            "version": "0.3.0",
            "status": "ok",
            "web_ui": False,
            "hint": "frontend/dist 缺失，当前仅提供 API；构建前端后可单端口打开完整界面",
        }

