"""LLM JSON 输出的稳健获取：宽松解析 + 截断修复 + 自动续写补全。

问题背景（frames L3/L5 与创作台长文曾反复踩坑）：
- 模型输出大 JSON 时容易触碰 max_tokens 上限被**截断在半句**，
  旧实现只会记 `{"layer_error": "模型未返回可解析 JSON"}`，
  用户侧表现为"拆解内容不完整"；
- 不同服务商/模型对 JSON 模式、围栏代码块的处理不一致。

本模块提供与具体模型无关的统一能力：
1. `parse_json_loose`：剥围栏 → 首尾括号截取 → **截断修复**（在最后一个完整元素处
   截断并补齐未闭合的 `]}`），尽力把"半截 JSON"救成可用结构；
2. `complete_json`：检测到截断（finish_reason=length 或触发修复）时，自动追加
   「继续输出剩余条目」请求并合并数组，直到 JSON 完整闭合或达到轮次上限。

所有失败都退化为"能拿到多少算多少"，不抛异常干扰主流程。
"""
from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

_FENCE_START = ("```json", "```JSON", "```")


def _strip_fence(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned[3:]
        if cleaned[:4].lower() == "json":
            cleaned = cleaned[4:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
    return cleaned.strip()


def _first_json_span(text: str) -> tuple[int, int]:
    """返回首个 `{` 与其后最后一个 `}` 的下标（无则 -1）。"""
    start = text.find("{")
    end = text.rfind("}")
    return start, end


def _repair_truncated(text: str) -> str | None:
    """在最后一个"完整值"处截断，并补齐未闭合的括号；无法修复返回 None。"""
    stack: list[str] = []
    in_str = False
    esc = False
    safe_points: list[tuple[int, tuple[str, ...]]] = []
    for i, ch in enumerate(text):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in "{[":
            stack.append(ch)
        elif ch in "}]":
            if stack:
                stack.pop()
            safe_points.append((i + 1, tuple(stack)))

    if not stack and not in_str:
        return text  # 本身已闭合，无需修复

    for pos, snap in reversed(safe_points):
        head = text[:pos].rstrip().rstrip(",")
        if not head:
            continue
        closing = "".join("}" if b == "{" else "]" for b in reversed(snap))
        candidate = head + closing
        try:
            json.loads(candidate)
            return candidate
        except json.JSONDecodeError:
            continue
    return None


def parse_json_loose(text: str) -> tuple[dict, bool]:
    """尽力解析；返回 (data, repaired)。data 为 {} 表示解析失败。"""
    if not text or not text.strip():
        return {}, False
    cleaned = _strip_fence(text)
    for candidate, repaired in ((cleaned, False), (None, True)):
        if candidate is None:
            start, end = _first_json_span(cleaned)
            if start < 0:
                break
            span = cleaned[start : end + 1] if end > start else cleaned[start:]
            candidate = _repair_truncated(span) or ""
        if not candidate:
            continue
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data, repaired
        if isinstance(data, list):
            return {"items": data}, repaired
    return {}, False


def _merge_arrays(base: dict, extra: dict, keys: list[str]) -> int:
    """把 extra 中的数组追加到 base（按 keys 顺序尝试），返回新增条目数。"""
    added = 0
    for key in keys:
        a, b = base.get(key), extra.get(key)
        if isinstance(a, list) and isinstance(b, list):
            base[key] = a + b
            added += len(b)
            break
    if added == 0:
        # 续写模型可能直接返回裸数组（无外层 key）
        b = extra.get("items")
        if isinstance(b, list) and b:
            for key in keys:
                if isinstance(base.get(key), list):
                    base[key] = base[key] + b
                    added += len(b)
                    break
    return added


CONTINUE_HINT = (
    "你上一次的输出因长度上限被截断，JSON 没有闭合。请**从中断处继续**输出剩余内容，"
    "只输出剩余的 JSON 片段（结构同上，不要重复已输出的条目，不要解释、不要客套）。"
    "若剩余条目仍很多，可适当精炼每条描述长度，但必须输出完整闭合的 JSON。"
)

CONCISE_HINT = (
    "上一次输出无法解析为完整 JSON。请重新输出一次：必须完整闭合，宁可精炼每条内容"
    "（每条描述控制在 60 字内），也不要中途截断或省略。"
)


async def complete_json(
    messages: list[dict],
    *,
    array_keys: list[str],
    model: str = "flash",
    temperature: float = 0.2,
    max_tokens: int = 8192,
    timeout: float = 300,
    scene: str = "misc",
    ref_type: str | None = None,
    ref_id: str | None = None,
    max_rounds: int = 3,
    json_mode: bool = True,
) -> tuple[dict, dict]:
    """多轮调用直至拿到完整 JSON；返回 (data, info)。

    info: {raw, finish_reason, model, rounds, repaired, truncated, usage}
    data 为 {} 表示彻底失败（调用失败时抛异常，交由上层处理）。
    """
    from app.ai import chat  # 延迟导入，避免模块级循环

    convo = list(messages)
    data: dict = {}
    last_result: dict = {}
    rounds = 0
    repaired_any = False
    truncated_any = False

    while rounds < max_rounds:
        rounds += 1
        result = await chat(
            messages=convo,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            json_mode=json_mode,
            timeout=timeout,
            scene=scene,
            ref_type=ref_type,
            ref_id=ref_id,
        )
        last_result = result
        raw = (result.get("reply") or "").strip()
        finish = result.get("finish_reason")
        if not raw:
            if rounds < max_rounds:
                convo = list(messages) + [{"role": "user", "content": CONCISE_HINT}]
                continue
            break

        chunk, repaired = parse_json_loose(raw)
        repaired_any = repaired_any or repaired
        truncated = finish == "length" or repaired
        truncated_any = truncated_any or truncated

        if not chunk:
            if rounds < max_rounds:
                convo = list(messages) + [{"role": "user", "content": CONCISE_HINT}]
                continue
            break

        if not data:
            data = chunk
        else:
            _merge_arrays(data, chunk, array_keys)

        if not truncated:
            break
        # 触发续写：带上已输出内容与续写指令
        convo = list(messages) + [
            {"role": "assistant", "content": raw},
            {"role": "user", "content": CONTINUE_HINT},
        ]
        if rounds == max_rounds:
            logger.info("complete_json 达到续写轮次上限(%s)，已尽力合并", max_rounds)

    info = {
        "raw": last_result.get("reply") or "",
        "finish_reason": last_result.get("finish_reason"),
        "model": last_result.get("model"),
        "rounds": rounds,
        "repaired": repaired_any,
        "truncated": truncated_any,
        "usage": last_result.get("usage"),
    }
    return data, info


def count_items(data: dict, array_keys: list[str]) -> int:
    for key in array_keys:
        v = data.get(key)
        if isinstance(v, list):
            return len(v)
    return 0


def dump_any(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False)
