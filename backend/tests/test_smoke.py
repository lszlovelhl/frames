"""冒烟测试：app 可 import、关键路由已注册。

边界说明：真实启动（lifespan）会在启动期做提示词落库/拉起后台任务，
冒烟测试只做到「import 无副作用 + 路由注册」这一层，
不启动真实服务、不连接任何数据库，避免污染业务库。
"""
from __future__ import annotations

import app.main as main


def _all_paths(app) -> list[str]:
    """收集 app.routes 中带 path 的路由（根 Mount 的 path 为空串，予以保留）。"""
    out: list[str] = []
    for r in app.routes:
        p = getattr(r, "path", None)
        if p is not None:
            out.append(p)
    return out


def _router_paths(router_module) -> list[str]:
    """收集 APIRouter(prefix=...) 内部路由的完整 path（已含 /api 前缀）。"""
    return [getattr(r, "path", "") for r in router_module.router.routes]


def test_app_importable():
    assert main.app.title == "帧间 Frames API"
    assert main.app.version == "0.3.0"


def test_key_router_groups_registered():
    from app.routers import (
        ai,
        billing,
        collection,
        creations,
        elements,
        platforms,
        products,
        prompts,
        providers,
        usage,
        videos,
    )

    for mod in (ai, videos, platforms, elements, creations, prompts, products, usage, providers, billing, collection):
        paths = _router_paths(mod)
        assert paths, f"{mod.__name__} 未注册任何路由"
        assert any(p.startswith("/api/") for p in paths), f"{mod.__name__} 缺少 /api 前缀路由"
    # 全局路由组中健康检查可达
    assert "/api/health/db" in _all_paths(main.app)


def test_root_route_present():
    # WEB_UI_ENABLED=True 时根路径由 SpaStaticFiles Mount("/") 承接，path 为空串
    assert any(p in ("/", "") for p in _all_paths(main.app))
