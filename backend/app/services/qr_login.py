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
            headless=True,
            args=[
                '--disable-blink-features=AutomationControlled',  # 反自动化检测
                '--no-sandbox',
                '--disable-dev-shm-usage',
            ]
        )
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800},
        )
        
        # 注入反检测脚本
        await context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            });
        """)
        page = await context.new_page()
        
        # 用domcontentloaded，不用networkidle，快很多，加20秒超时
        await page.goto(login_urls[platform], wait_until="domcontentloaded", timeout=20000)
        await asyncio.sleep(3)  # 等页面加载和二维码渲染
        
        # 截图二维码区域 - 通用逻辑：找正方形的img
        if platform == "bilibili":
            qr_bytes = None
            # 找所有img，找正方形的
            imgs = await page.query_selector_all("img")
            best_qr = None
            best_score = 0
            for img in imgs:
                box = await img.bounding_box()
                if not box:
                    continue
                w, h = box["width"], box["height"]
                # 只要是正方形，宽高在80-500之间就行
                if w < 80 or h < 80 or w > 500 or h > 500:
                    continue
                if abs(w - h) > w * 0.2:  # 宽高差不超过20%
                    continue
                # 优先选尺寸200左右的（二维码一般是200x200）
                score = 100 - abs(w - 200)
                if score > best_score:
                    best_score = score
                    best_qr = img
            
            if best_qr:
                qr_bytes = await best_qr.screenshot()
            
            # 找不到就试canvas
            if not qr_bytes:
                canvases = await page.query_selector_all("canvas")
                for c in canvases:
                    box = await c.bounding_box()
                    if box and box["width"] > 80:
                        qr_bytes = await c.screenshot()
                        break
            
            # 都找不到就整个页面截图
            if not qr_bytes:
                qr_bytes = await page.screenshot()
        elif platform == "douyin":
            # 抖音：先点登录按钮，弹出登录弹窗
            try:
                # 等页面加载完，找登录按钮
                await asyncio.sleep(2)
                # 尝试点登录按钮
                login_btn = await page.query_selector('div:has-text("登录"), button:has-text("登录")')
                if login_btn:
                    await login_btn.click()
                
                # 等二维码出现，最多等10秒
                qr_bytes = None
                for wait_i in range(10):
                    await asyncio.sleep(1)
                    # 从弹窗里找正方形二维码
                    imgs = await page.query_selector_all("img")
                    best_qr = None
                    best_score = 0
                    for img in imgs:
                        box = await img.bounding_box()
                        if not box:
                            continue
                        w, h = box["width"], box["height"]
                        if w < 150 or h < 150 or w > 250 or h > 250:
                            continue
                        if abs(w - h) > w * 0.1:
                            continue
                        score = 100 - abs(w - 180)
                        if score > best_score:
                            best_score = score
                            best_qr = img
                    
                    if best_qr:
                        qr_bytes = await best_qr.screenshot()
                        break
                
                # 找不到试canvas
                if not qr_bytes:
                    canvases = await page.query_selector_all("canvas")
                    for c in canvases:
                        box = await c.bounding_box()
                        if box and 150 < box["width"] < 250:
                            qr_bytes = await c.screenshot()
                            break
                
                # 都找不到才整页截图
                if not qr_bytes:
                    qr_bytes = await page.screenshot()
            except Exception as e:
                logger.warning("抖音登录弹窗处理失败: %s", e)
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
    """后台轮询，检查是否登录成功，同时定时刷新二维码。"""
    session = _active_sessions.get(session_id)
    if not session:
        return
    
    page = session["page"]
    platform = session["platform"]
    
    try:
        for i in range(60):  # 最多等60秒
            await asyncio.sleep(1)
            
            # 每3秒刷新一次二维码（B站二维码会自动刷新）
            if i % 3 == 0 and session["status"] == "waiting":
                try:
                    qr_bytes = None
                    if platform == "bilibili":
                        # 找正方形img，选最优的
                        imgs = await page.query_selector_all("img")
                        best_qr = None
                        best_score = 0
                        for img in imgs:
                            box = await img.bounding_box()
                            if not box:
                                continue
                            w, h = box["width"], box["height"]
                            if w < 80 or h < 80 or w > 500 or h > 500:
                                continue
                            if abs(w - h) > w * 0.2:
                                continue
                            score = 100 - abs(w - 200)
                            if score > best_score:
                                best_score = score
                                best_qr = img
                        
                        if best_qr:
                            qr_bytes = await best_qr.screenshot()
                        
                        # 找不到试canvas
                        if not qr_bytes:
                            canvases = await page.query_selector_all("canvas")
                            for c in canvases:
                                box = await c.bounding_box()
                                if box and box["width"] > 80:
                                    qr_bytes = await c.screenshot()
                                    break
                        
                        # 都找不到才整页截图
                        if not qr_bytes:
                            qr_bytes = await page.screenshot()
                    elif platform == "douyin":
                        # 抖音：从登录弹窗里找正方形二维码
                        qr_bytes = None
                        imgs = await page.query_selector_all("img")
                        best_qr = None
                        best_score = 0
                        for img in imgs:
                            box = await img.bounding_box()
                            if not box:
                                continue
                            w, h = box["width"], box["height"]
                            # 二维码尺寸：150-250之间，正方形
                            if w < 150 or h < 150 or w > 250 or h > 250:
                                continue
                            if abs(w - h) > w * 0.1:
                                continue
                            # 排除头像（头像一般有圆角或者是圆形的，但我们先靠尺寸判断）
                            score = 100 - abs(w - 180)
                            if score > best_score:
                                best_score = score
                                best_qr = img
                        
                        if best_qr:
                            qr_bytes = await best_qr.screenshot()
                        
                        # 找不到试canvas
                        if not qr_bytes:
                            canvases = await page.query_selector_all("canvas")
                            for c in canvases:
                                box = await c.bounding_box()
                                if box and 150 < box["width"] < 250:
                                    qr_bytes = await c.screenshot()
                                    break
                        
                        # 如果找不到二维码了，说明进入了验证步骤
                        if not qr_bytes:
                            # 如果刚进入验证状态
                            if session["status"] == "waiting":
                                session["status"] = "waiting_verify"
                                try:
                                    # 自动点击"手机验证"、"短信登录"选项，切换到验证码登录
                                    buttons = await page.query_selector_all("div, span, button")
                                    for btn in buttons:
                                        text = await btn.inner_text()
                                        text = text.strip()
                                        if "手机" in text or "短信" in text or "验证码" in text:
                                            if len(text) < 15:
                                                await btn.click()
                                                logger.info("已自动切换到验证方式: %s", text)
                                                await asyncio.sleep(2)  # 等页面加载
                                                break
                                except Exception as e:
                                    logger.warning("切换验证方式失败: %s", e)
                                
                                # 截整个验证页面
                                qr_bytes = await page.screenshot()
                                session["qr_image"] = f"data:image/png;base64,{base64.b64encode(qr_bytes).decode('utf-8')}"
                        else:
                            # 找到新二维码了，恢复waiting状态
                            session["status"] = "waiting"
                    else:
                        qr_bytes = await page.screenshot()
                    
                    if qr_bytes:
                        qr_base64 = base64.b64encode(qr_bytes).decode("utf-8")
                        session["qr_image"] = f"data:image/png;base64,{qr_base64}"
                except Exception:
                    pass  # 截图失败不影响主流程
            
            # 检查是否登录成功：更严格的验证
            cookies = await session["context"].cookies()
            cookie_names = [c["name"] for c in cookies]
            
            login_success = False
            if platform == "bilibili":
                # B站登录成功后有SESSDATA，再请求接口确认
                if "SESSDATA" in cookie_names:
                    try:
                        import httpx
                        cookie_dict = {c["name"]: c["value"] for c in cookies}
                        async with httpx.AsyncClient(timeout=5.0, cookies=cookie_dict) as client:
                            resp = await client.get("https://api.bilibili.com/x/web-interface/nav")
                            data = resp.json()
                            if data.get("code") == 0 and data.get("data", {}).get("isLogin"):
                                login_success = True
                    except:
                        pass
            else:
                # 抖音：不能只看cookie，要确认真的能拿到用户信息
                if "sessionid" in cookie_names or "sessionid_ss" in cookie_names:
                    try:
                        import httpx
                        cookie_dict = {c["name"]: c["value"] for c in cookies}
                        async with httpx.AsyncClient(timeout=5.0, cookies=cookie_dict) as client:
                            resp = await client.get("https://www.douyin.com/aweme/v1/web/user/profile/self/")
                            data = resp.json()
                            # 必须真的拿到用户数据才算登录成功
                            if data.get("status_code") == 0 and data.get("user") and data["user"].get("nickname"):
                                login_success = True
                    except:
                        pass
            
            if login_success:
                await asyncio.sleep(2)  # 等cookie加载完整
                
                # 导出cookie
                cookie_text = _cookies_to_netscape(cookies)
                
                # 保存
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
    """查询登录状态，同时返回最新的二维码图片。"""
    session = _active_sessions.get(session_id)
    if not session:
        return {"status": "not_found"}
    
    return {
        "session_id": session_id,
        "status": session["status"],
        "platform": session["platform"],
        "qr_image": session.get("qr_image"),  # 最新的二维码截图
    }


async def submit_verify_code(session_id: str, code: str) -> dict:
    """用户输入手机验证码，自动填入页面并提交。"""
    session = _active_sessions.get(session_id)
    if not session:
        return {"ok": False, "message": "会话不存在"}
    
    page = session.get("page")
    if not page:
        return {"ok": False, "message": "页面不存在"}
    
    try:
        # 找验证码输入框
        inputs = await page.query_selector_all("input")
        code_input = None
        for inp in inputs:
            placeholder = await inp.get_attribute("placeholder") or ""
            type_ = await inp.get_attribute("type") or ""
            if "验证码" in placeholder or "code" in type_.lower():
                code_input = inp
                break
        
        if not code_input:
            return {"ok": False, "message": "找不到验证码输入框"}
        
        # 填入验证码
        await code_input.fill(code)
        await asyncio.sleep(0.5)
        
        # 找确认按钮并点击
        buttons = await page.query_selector_all("button")
        for btn in buttons:
            text = await btn.inner_text()
            if "确认" in text or "登录" in text or "提交" in text:
                await btn.click()
                break
        
        await asyncio.sleep(3)  # 等提交
        
        return {"ok": True, "message": "验证码已提交，请等待登录结果"}
    except Exception as e:
        return {"ok": False, "message": f"提交验证码失败: {str(e)}"}


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
