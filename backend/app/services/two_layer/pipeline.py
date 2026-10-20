"""两层简化 pipeline：
  L1：一次调用出整体框架（结构化 JSON）
    ↓
  Harness：确定性后处理（时间对齐、格式校验）
    ↓
  L2：一次调用出完整成稿（Markdown 编导脚本）
"""

import json
import logging
from typing import Any

from app.ai import chat
from app.services.llm_json import parse_json_loose
from .prompts_l1 import L1_SYSTEM, L1_USER_TEMPLATE
from .prompts_l2 import L2_SYSTEM, L2_USER_TEMPLATE

logger = logging.getLogger(__name__)


async def call_flash_llm(prompt: str, system: str = "", max_tokens: int = 4096, model: str = "flash") -> str:
    """调用云端 flash/fast 档（用于 L1 框架抽取）"""
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    result = await chat(
        messages=messages,
        model=model,
        temperature=0.3,
        max_tokens=max_tokens,
        json_mode=True,
        timeout=180,
        scene="two_l1_framework",
    )
    return result.get("reply", "")


async def call_pro_llm(prompt: str, system: str = "", max_tokens: int = 8192, model: str = "pro") -> str:
    """调用 pro 档模型（用于 L2 成稿输出）"""
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    result = await chat(
        messages=messages,
        model=model,
        temperature=0.3,
        max_tokens=max_tokens,
        timeout=300,
        scene="two_l2_script",
    )
    return result.get("reply", "")


async def run_l1(transcript: str, scenes: str, duration_sec: int, model: str = "flash") -> dict[str, Any]:
    """L1：一次调用出整体框架（结构化 JSON）"""
    user_prompt = L1_USER_TEMPLATE.format(
        duration_sec=duration_sec,
        transcript=transcript,
        scenes=scenes,
    )

    logger.info("L1：调用模型 %s 出框架...", model)
    raw_output = await call_flash_llm(user_prompt, system=L1_SYSTEM, max_tokens=4096, model=model)

    l1_data, repaired = parse_json_loose(raw_output)
    if not l1_data:
        logger.warning("L1 JSON 解析失败，尝试从文本截取: %s", raw_output[:200])
        l1_data = {"segments": [], "emotion_curve": [], "elements": []}

    logger.info(
        "L1 完成 (repaired=%s)：%d 段，%d 情绪点，%d 元素",
        repaired,
        len(l1_data.get("segments", [])),
        len(l1_data.get("emotion_curve", [])),
        len(l1_data.get("elements", [])),
    )
    return l1_data


def run_harness(l1_data: dict[str, Any], duration_sec: int) -> dict[str, Any]:
    """Harness：确定性后处理"""
    # 1. 时间覆盖校验——确保覆盖全片
    segments = l1_data.get("segments", [])
    if segments:
        last_end = segments[-1].get("end_sec", 0)
        if last_end < duration_sec - 5:
            logger.warning("时间覆盖不足：最后一段 %ss，视频 %ss", last_end, duration_sec)
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
            emotion_curve.append({
                "t_sec": duration_sec,
                "intensity": emotion_curve[-1].get("intensity", 5),
                "direction": emotion_curve[-1].get("direction", "平静"),
            })

    l1_data["segments"] = segments
    l1_data["emotion_curve"] = emotion_curve
    return l1_data


async def run_l2(l1_data: dict[str, Any], transcript: str, scenes: str, model: str = "pro") -> str:
    """L2：一次调用出完整成稿（Markdown）"""
    user_prompt = L2_USER_TEMPLATE.format(
        l1_json=json.dumps(l1_data, ensure_ascii=False, indent=2),
        transcript=transcript,
        scenes=scenes,
    )

    logger.info("L2：调用模型 %s 出成稿...", model)
    markdown = await call_pro_llm(user_prompt, system=L2_SYSTEM, max_tokens=8192, model=model)
    logger.info("L2 完成：%d 字符", len(markdown))
    return markdown


async def run_pipeline(
    transcript: str, scenes: str, duration_sec: int, model: str = "flash"
) -> dict[str, Any]:
    """主流程：L1 → Harness → L2 (纯异步，无阻塞)"""
    logger.info("开始两层 pipeline：%ds 视频", duration_sec)

    # L1：出框架
    l1_data = await run_l1(transcript, scenes, duration_sec, model=model)

    # Harness：后处理
    l1_data = run_harness(l1_data, duration_sec)

    # L2：出成稿
    markdown = await run_l2(l1_data, transcript, scenes, model="pro" if model == "flash" else model)

    return {
        "l1_data": l1_data,
        "markdown": markdown,
    }

