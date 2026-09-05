"""采集账号（平台登录态）管理 API。

提供：
- GET    /api/platforms             全部平台登录态列表
- POST   /api/platforms/{platform}/cookies   保存/更新登录态（cookies.txt 文本）
- DELETE /api/platforms/{platform}/cookies   清除登录态
- POST   /api/platforms/{platform}/detect    立即检测登录态状态
- GET    /api/platforms/{platform}/login-url 平台登录地址（供前端跳转）
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.media import cookies

router = APIRouter(prefix="/api/platforms", tags=["platforms"])


class CookieBody(BaseModel):
    cookies_text: str


@router.get("")
async def list_platforms():
    return cookies.list_status()


@router.post("/{platform}/cookies")
async def save_platform_cookies(platform: str, body: CookieBody):
    try:
        return cookies.save_cookies(platform, body.cookies_text)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{platform}/detect")
async def detect_platform(platform: str):
    st = cookies.get_status(platform)
    # 结构性检查在 get_status 内已自动完成；这里额外给出人工复核指引
    return {
        **st,
        "detected_at": cookies._now(),
        "hint": "登录态真实验证以最近一次抓取结果为准：抓取成功自动标记有效，失效自动标记过期。",
    }


@router.delete("/{platform}/cookies")
async def clear_platform_cookies(platform: str):
    cookies.clear_cookies(platform)
    return {"ok": True, **cookies.get_status(platform)}


@router.get("/{platform}/login-url")
async def platform_login_url(platform: str):
    for p in cookies.PLATFORMS:
        if p["platform"] == platform:
            return {"platform": platform, "login_url": p["login_url"], "label": p["label"]}
    raise HTTPException(status_code=404, detail="平台不存在")
