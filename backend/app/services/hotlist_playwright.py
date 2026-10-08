"""
Playwright版热榜爬虫
- 用系统Chrome浏览器
- 无头模式运行
- 自动处理弹窗
- 检测登录态
"""
import logging
import re
from typing import Any

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

logger = logging.getLogger(__name__)


def parse_count(text: str) -> int:
    """把'257.7万'这样的字符串转换成数字"""
    text = text.strip()
    if not text:
        return 0
    # 处理万
    if "万" in text:
        num = float(text.replace("万", ""))
        return int(num * 10000)
    # 处理亿
    if "亿" in text:
        num = float(text.replace("亿", ""))
        return int(num * 100000000)
    # 纯数字
    try:
        return int(text.replace(",", ""))
    except:
        return 0


def fetch_bilibili_hotlist_playwright(headless: bool = True) -> list[dict[str, Any]]:
    """用Playwright爬取B站热门排行榜"""
    results = []

    with sync_playwright() as p:
        # 启动系统Chrome
        browser = p.chromium.launch(
            channel="chrome",
            headless=headless,
            args=["--disable-blink-features=AutomationControlled"],  # 隐藏自动化特征
        )

        # 创建上下文，带一些真实浏览器的指纹
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800},
            locale="zh-CN",
        )

        page = context.new_page()

        try:
            # 打开B站热门排行榜
            page.goto("https://www.bilibili.com/v/popular/rank/all", wait_until="networkidle", timeout=30000)

            # 自动处理弹窗（登录弹窗、广告弹窗等）
            # 先等一下页面加载
            page.wait_for_timeout(2000)

            # 尝试关闭可能出现的登录弹窗
            try:
                close_btn = page.locator(".bili-mini-close, .close-dialog, [class*='close']").first
                if close_btn.is_visible(timeout=1000):
                    close_btn.click()
                    page.wait_for_timeout(500)
            except Exception:
                pass

            # 滚动一下页面，加载更多内容
            page.evaluate("window.scrollTo(0, 500)")
            page.wait_for_timeout(1000)

            # 爬取视频列表
            # B站热门排行榜的视频卡片选择器
            video_cards = page.locator(".rank-item").all()

            logger.info(f"找到 {len(video_cards)} 个视频卡片")

            for i, card in enumerate(video_cards[:30]):  # 最多取30个
                try:
                    # 标题
                    title_el = card.locator(".title").first
                    title = title_el.inner_text().strip() if title_el.count() > 0 else ""

                    # 链接
                    link_el = card.locator("a.title").first
                    href = link_el.get_attribute("href") if link_el.count() > 0 else ""
                    if href and href.startswith("//"):
                        href = "https:" + href

                    # 封面图
                    cover_el = card.locator(".cover").first
                    cover_url = cover_el.get_attribute("data-src") or cover_el.get_attribute("src") if cover_el.count() > 0 else ""
                    if cover_url and cover_url.startswith("//"):
                        cover_url = "https:" + cover_url

                    # UP主
                    up_el = card.locator(".up-name").first
                    up_name = up_el.inner_text().strip() if up_el.count() > 0 else ""

                    # 播放量和弹幕数（从data-box里解析）
                    play_count = 0
                    danmaku_count = 0
                    data_boxes = card.locator(".data-box").all()
                    for box in data_boxes:
                        text = box.inner_text().strip()
                        # 简单判断：带万的就是播放量，另一个是弹幕
                        if "万" in text or "." in text:
                            if play_count == 0:
                                play_count = parse_count(text)
                            else:
                                danmaku_count = parse_count(text)

                    if title and href:
                        results.append({
                            "rank": i + 1,
                            "title": title,
                            "video_url": href,
                            "cover_url": cover_url,
                            "author": up_name,
                            "play_count": play_count,
                            "danmaku_count": danmaku_count,
                            "hot_value": play_count,  # 用播放量作为热度值
                        })
                except Exception as e:
                    logger.warning(f"解析第{i+1}个视频失败: {e}")
                    continue

        except PlaywrightTimeout:
            logger.error("打开B站热门页面超时")
        except Exception as e:
            logger.error(f"爬取B站热榜失败: {e}")
        finally:
            context.close()
            browser.close()

    return results


def fetch_douyin_hotlist_playwright(headless: bool = True) -> list[dict[str, Any]]:
    """用Playwright爬取抖音热搜榜"""
    results = []

    with sync_playwright() as p:
        browser = p.chromium.launch(
            channel="chrome",
            headless=headless,
            args=["--disable-blink-features=AutomationControlled"],
        )

        context = browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800},
            locale="zh-CN",
        )

        page = context.new_page()

        try:
            # 打开抖音热搜榜页面
            page.goto("https://www.douyin.com/hot", wait_until="networkidle", timeout=30000)
            page.wait_for_timeout(3000)

            # 自动处理弹窗
            try:
                close_btn = page.locator("[class*='close'], .modal-close").first
                if close_btn.is_visible(timeout=1000):
                    close_btn.click()
                    page.wait_for_timeout(500)
            except Exception:
                pass

            # 爬取热搜列表
            hot_items = page.locator("[class*='hot-item'], [class*='search-item']").all()
            logger.info(f"找到 {len(hot_items)} 个热搜项")

            for i, item in enumerate(hot_items[:30]):
                try:
                    title_el = item.locator("a, span, div").first
                    title = title_el.inner_text().strip() if title_el.count() > 0 else ""
                    if title:
                        results.append({
                            "rank": i + 1,
                            "title": title,
                            "hot_value": 0,
                        })
                except Exception:
                    continue

        except Exception as e:
            logger.error(f"爬取抖音热榜失败: {e}")
        finally:
            context.close()
            browser.close()

    return results


if __name__ == "__main__":
    # 测试
    print("=== 测试B站热榜 ===")
    bili_items = fetch_bilibili_hotlist_playwright(headless=True)
    print(f"拿到 {len(bili_items)} 条")
    for item in bili_items[:5]:
        print(f"  {item['rank']}. {item['title']} - {item['video_url']}")

    print("\n=== 测试抖音热榜 ===")
    douyin_items = fetch_douyin_hotlist_playwright(headless=True)
    print(f"拿到 {len(douyin_items)} 条")
    for item in douyin_items[:5]:
        print(f"  {item['rank']}. {item['title']}")
