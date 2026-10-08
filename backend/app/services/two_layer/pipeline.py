"""
两层简化 pipeline：
  L1：一次调用出整体框架（结构化 JSON）
    ↓
  Harness：确定性后处理（时间对齐、格式校验）
    ↓
  L2：一次调用出完整成稿（Markdown 编导脚本）
"""

import json
import logging
from typing import Any
import urllib.request

from .prompts_l1 import L1_SYSTEM, L1_USER_TEMPLATE
from .prompts_l2 import L2_SYSTEM, L2_USER_TEMPLATE
from app.core.config import GLM_API_KEY

logger = logging.getLogger(__name__)

OLLAMA_URL = "http://localhost:11434/v1/chat/completions"
LOCAL_MODEL = "qwen3.5:latest"

# 智谱 glm-4-flash（免费档）— API Key 从 backend/.env 的 GLM_API_KEY 读取，禁止硬编码
GLM_URL = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
GLM_MODEL = "glm-4-flash"


def _post_json(url: str, payload: dict, headers: dict = None, timeout: int = 120) -> dict:
    """用 urllib 发 POST JSON 请求"""
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def call_local_llm(prompt: str, system: str = "", max_tokens: int = 16384) -> str:
    """调用本地 qwen3.5（慢但质量好，用于 L2 成稿）"""
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    result = _post_json(
        OLLAMA_URL,
        payload={
            "model": LOCAL_MODEL,
            "messages": messages,
            "temperature": 0.3,
            "max_tokens": max_tokens,
        },
        timeout=600,
    )

    content = result["choices"][0]["message"].get("content", "")
    return content


def call_flash_llm(prompt: str, system: str = "", max_tokens: int = 4096) -> str:
    """调用云端 glm-4-flash（快，免费，用于 L1 框架）"""
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    result = _post_json(
        GLM_URL,
        headers={"Authorization": f"Bearer {GLM_API_KEY}"},
        payload={
            "model": GLM_MODEL,
            "messages": messages,
            "temperature": 0.3,
            "max_tokens": max_tokens,
        },
        timeout=180,
    )

    content = result["choices"][0]["message"].get("content", "")
    return content


def run_l1(transcript: str, scenes: str, duration_sec: int) -> dict[str, Any]:
    """L1：一次调用出整体框架（结构化 JSON）"""
    user_prompt = L1_USER_TEMPLATE.format(
        duration_sec=duration_sec,
        transcript=transcript,
        scenes=scenes,
    )

    logger.info("L1：调用云端 flash 出框架...")
    raw_output = call_flash_llm(user_prompt, system=L1_SYSTEM, max_tokens=4096)

    # 解析 JSON
    # 去掉可能的 ```json 包裹
    raw_output = raw_output.strip()
    if raw_output.startswith("```json"):
        raw_output = raw_output[7:]
    if raw_output.startswith("```"):
        raw_output = raw_output[3:]
    if raw_output.endswith("```"):
        raw_output = raw_output[:-3]
    raw_output = raw_output.strip()

    l1_data = json.loads(raw_output)
    logger.info(f"L1 完成：{len(l1_data.get('segments', []))} 段，{len(l1_data.get('sentences', []))} 句")

    return l1_data


def run_harness(l1_data: dict[str, Any], duration_sec: int) -> dict[str, Any]:
    """Harness：确定性后处理"""
    # 1. 时间覆盖校验——确保覆盖全片
    segments = l1_data.get("segments", [])
    if segments:
        # 最后一段的 end_sec 应该接近视频时长
        last_end = segments[-1].get("end_sec", 0)
        if last_end < duration_sec - 5:
            logger.warning(f"时间覆盖不足：最后一段 {last_end}s，视频 {duration_sec}s")
            # 补一个收尾段
            segments.append({
                "title": "收尾",
                "start_sec": last_end,
                "end_sec": duration_sec,
                "purpose": "画面收尾，留记忆点",
                "summary": "最后画面淡出，结束",
            })

    # 2. 情绪曲线补点——确保覆盖全片
    emotion_curve = l1_data.get("emotion_curve", [])
    if emotion_curve:
        last_t = emotion_curve[-1].get("t_sec", 0)
        if last_t < duration_sec - 5:
            # 补点
            emotion_curve.append({
                "t_sec": duration_sec,
                "intensity": emotion_curve[-1].get("intensity", 5),
                "direction": emotion_curve[-1].get("direction", "平静"),
            })

    l1_data["segments"] = segments
    l1_data["emotion_curve"] = emotion_curve

    return l1_data


def run_l2(l1_data: dict[str, Any], transcript: str, scenes: str) -> str:
    """L2：一次调用出完整成稿（Markdown）"""
    user_prompt = L2_USER_TEMPLATE.format(
        l1_json=json.dumps(l1_data, ensure_ascii=False, indent=2),
        transcript=transcript,
        scenes=scenes,
    )

    logger.info("L2：调用本地 qwen3.5 出成稿...")
    markdown = call_local_llm(user_prompt, system=L2_SYSTEM, max_tokens=8192)

    logger.info(f"L2 完成：{len(markdown)} 字符")
    return markdown


def run_pipeline(transcript: str, scenes: str, duration_sec: int) -> dict[str, Any]:
    """主流程：L1 → Harness → L2"""
    logger.info(f"开始两层 pipeline：{duration_sec}s 视频")

    # L1：出框架
    l1_data = run_l1(transcript, scenes, duration_sec)

    # Harness：后处理
    l1_data = run_harness(l1_data, duration_sec)

    # L2：出成稿
    markdown = run_l2(l1_data, transcript, scenes)

    return {
        "l1_data": l1_data,
        "markdown": markdown,
    }
