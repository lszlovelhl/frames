"""扫码登录服务：用Playwright打开登录页，自动获取cookie。"""
import asyncio
import base64
import logging
import uuid
from pathlib import Path

from app.media import cookies as cookie_store

logger = logging.getLogger(__name__)

# 活跃的登录会话
_active_sessions: dict[str, dict] = {}


async def start_qr_login(platform: str) -> dict:
    """启动扫码登录流程，返回会话ID和二维码图片。"""
    session_id = uuid.uuid4().hex
    
    # 登录URL配置
    login_urls = {
        "bilibili": "https://passport.bilibili.com/login",
        "douyin": "https://www.douyin.com/",
    }
    
    if platform not in login_urls:
        raise ValueError(f"不支持平台 {platform}")
    
    session = {
        "platform": platform,
        "status": "waiting",  # waiting / scanning / success / expired
        "qr_image": None,
        "page": None,
        "browser": None,
        "context": None,
    }
    
    try:
        from playwright.async_api import async_playwright
        
        pw = await async_playwright().start()
        browser = await pw.chromium.launch(
            channel="chrome",
            headless=True,  # 无头模式，截图给前端
        )
        context = await browser.new_context()
        page = await context.new_page()
        
        await page.goto(login_urls[platform], wait_until="networkidle")
        
        # 等待二维码加载
        await asyncio.sleep(2)
        
        # 截图二维码区域
        if platform == "bilibili":
            # B站二维码选择器
            qr = await page.query_selector(".qrcode-img, .login-qr img")
            if qr:
                qr_bytes = await qr.screenshot()
            else:
                # 整个页面截图
                qr_bytes = await page.screenshot()
        else:
            qr_bytes = await page.screenshot()
        
        qr_base64 = base64.b64encode(qr_bytes).decode("utf-8")
        
        session["qr_image"] = f"data:image/png;base64,{qr_base64}"
        session["page"] = page
        session["browser"] = browser
        session["context"] = context
        session["pw"] = pw
        
        _active_sessions[session_id] = session
        
        # 启动后台任务，检测登录状态
        asyncio.create_task(_check_login_status(session_id))
        
        return {
            "session_id": session_id,
            "qr_image": session["qr_image"],
            "status": "waiting",
        }
        
    except Exception as e:
        logger.exception("启动扫码登录失败")
        if session.get("browser"):
            await session["browser"].close()
        raise


async def _check_login_status(session_id: str):
    """后台轮询，检查是否登录成功。"""
    session = _active_sessions.get(session_id)
    if not session:
        return
    
    page = session["page"]
    platform = session["platform"]
    
    try:
        for i in range(60):  # 最多等60秒
            await asyncio.sleep(1)
            
            # 检查URL是否跳转（登录成功后通常会跳转）
            current_url = page.url
            
            if platform == "bilibili":
                # B站登录成功后会跳转到www.bilibili.com
                if "passport.bilibili.com" not in current_url:
                    # 登录成功！
                    await asyncio.sleep(2)  # 等cookie加载
                    
                    # 导出cookie
                    cookies = await session["context"].cookies()
                    
                    # 转成Netscape格式
                    cookie_text = _cookies_to_netscape(cookies)
                    
                    # 保存
                    cookie_store.save_cookies(platform, cookie_text)
                    
                    session["status"] = "success"
                    logger.info(f"扫码登录成功: {platform}")
                    break
            else:
                # 抖音登录检测
                if "login" not in current_url:
                    await asyncio.sleep(2)
                    cookies = await session["context"].cookies()
                    cookie_text = _cookies_to_netscape(cookies)
                    cookie_store.save_cookies(platform, cookie_text)
                    session["status"] = "success"
                    logger.info(f"扫码登录成功: {platform}")
                    break
        
        if session["status"] != "success":
            session["status"] = "expired"
            
    except Exception as e:
        logger.exception("检查登录状态失败")
        session["status"] = "expired"
    finally:
        # 清理资源
        try:
            if session.get("browser"):
                await session["browser"].close()
            if session.get("pw"):
                await session["pw"].stop()
        except:
            pass


def get_qr_login_status(session_id: str) -> dict:
    """查询登录状态。"""
    session = _active_sessions.get(session_id)
    if not session:
        return {"status": "not_found"}
    
    return {
        "session_id": session_id,
        "status": session["status"],
        "platform": session["platform"],
    }


def _cookies_to_netscape(cookies: list[dict]) -> str:
    """把Playwright的cookie列表转成Netscape格式。"""
    lines = ["# Netscape HTTP Cookie File"]
    for c in cookies:
        domain = c.get("domain", "")
        include_flag = "TRUE" if domain.startswith(".") else "FALSE"
        path = c.get("path", "/")
        secure = "TRUE" if c.get("secure") else "FALSE"
        expires = str(int(c.get("expires", 0))) if c.get("expires") else "0"
        name = c.get("name", "")
        value = c.get("value", "")
        lines.append(f"{domain}\t{include_flag}\t{path}\t{secure}\t{expires}\t{name}\t{value}")
    return "\n".join(lines)
