"""从分享链接识别平台。"""
import re

PATTERNS: list[tuple[str, re.Pattern]] = [
    ("bilibili", re.compile(r"(bilibili\.com|b23\.tv|bili22\.com|bili33\.com)", re.I)),
    ("youtube", re.compile(r"(youtube\.com|youtu\.be|youtube-nocookie\.com)", re.I)),
    ("douyin", re.compile(r"(douyin\.com|iesdouyin\.com)", re.I)),
    ("xiaohongshu", re.compile(r"(xiaohongshu\.com|xhslink\.com)", re.I)),
    ("wechat", re.compile(r"(channels\.weixin\.qq\.com|weixin\.qq\.com/sph)", re.I)),
]

PLATFORM_NAMES = {
    "bilibili": "哔哩哔哩",
    "youtube": "YouTube",
    "douyin": "抖音",
    "xiaohongshu": "小红书",
    "wechat": "微信视频号",
}


def detect_platform(url: str) -> str:
    if not url:
        return "unknown"
    for name, pattern in PATTERNS:
        if pattern.search(url):
            return name
    return "unknown"


def platform_label(platform: str) -> str:
    return PLATFORM_NAMES.get(platform, platform)
