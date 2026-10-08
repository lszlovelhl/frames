"""热榜AI分析 - 用大模型总结热榜趋势"""
import json
import logging
import urllib.request

logger = logging.getLogger(__name__)

GLM_API_KEY = "d2bfe44483624780bb196486d2594cf6.zd0D6UARdQUeWZXq"
GLM_URL = "https://open.bigmodel.cn/api/paas/v4/chat/completions"


async def analyze_hotlist(items: list[dict]) -> str:
    """用AI分析热榜，生成趋势总结和选题建议"""

    # 整理热榜内容
    hotlist_text = "\n".join([
        f"{item['rank']}. [{item['platform']}] {item['title']} (热度: {item['hot_value']})"
        for item in items
    ])

    prompt = f"""你是一个短视频编导专家。请分析下面的今日热榜，给出：

1. **今日热点总结**（200字以内）：今天的热点主要集中在哪些领域？有什么共同特征？
2. **爆款选题建议**（5条）：从编导角度，哪些热点适合做成短视频？为什么？
3. **趋势观察**：和前几天比，今天有什么新的热点方向？

热榜内容：
{hotlist_text}

请用简洁专业的语言回答，不要空话套话。"""

    try:
        payload = {
            "model": "glm-4-flash",
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 2000,
            "temperature": 0.7,
        }

        req = urllib.request.Request(
            GLM_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {GLM_API_KEY}",
                "Content-Type": "application/json",
            },
        )

        with urllib.request.urlopen(req, timeout=60) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            content = result["choices"][0]["message"]["content"]
            logger.info("热榜AI分析完成，长度: %d", len(content))
            return content

    except Exception as e:
        logger.error("热榜AI分析失败: %s", e)
        return ""
