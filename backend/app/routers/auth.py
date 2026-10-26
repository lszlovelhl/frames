"""认证 API：注册/登录/登出/当前用户。本地账号体系，数据不上云。"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.services import auth as auth_service

logger = logging.getLogger(__name__)
router = APIRouter(tags=["auth"])


class RegisterRequest(BaseModel):
    username: str
    password: str
    display_name: str = ""


class LoginRequest(BaseModel):
    username: str
    password: str


@router.post("/api/auth/register")
async def register(
    req: RegisterRequest,
    db: AsyncSession = Depends(get_session),
):
    return await auth_service.register(db, req.username, req.password, req.display_name)


@router.post("/api/auth/login")
async def login(
    req: LoginRequest,
    db: AsyncSession = Depends(get_session),
):
    return await auth_service.login(db, req.username, req.password)


@router.post("/api/auth/logout")
async def logout(
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_session),
):
    token = authorization.removeprefix("Bearer ").strip() if authorization else ""
    await auth_service.logout(db, token)
    return {"ok": True}


@router.get("/api/auth/me")
async def me(
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_session),
):
    token = authorization.removeprefix("Bearer ").strip() if authorization else ""
    user = await auth_service.resolve_token(db, token)
    if not user:
        return {"ok": False, "message": "未登录"}
    return {"ok": True, **user}
