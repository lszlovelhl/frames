"""结构线（L3）时间锚点回填。

背景：转写文本已是逐句 ``[起-止] 原话`` 形式，但模型（尤其免费档 flash）经常
无视"start_ms/end_ms 必须取转写真实时间戳"的契约，把段落时间全填 0，导致结构线
失去时间轴意义。仅靠提示词无法保证，这里做**确定性回填**，优先级：

1. ``lines``：模型若按契约回填了该段覆盖的转写行号区间（``[起行, 止行]``，1-based
   闭区间），直接换算为时间戳——最可靠；
2. 原话/摘要匹配：用段落里的原话引用（``quote`` 等）或摘要与转写行做匹配，命中
   连续行区间则取该区间首行 start、末行 end；
3. 顺序均分（estimated）：以上都拿不到时，按段落顺序在视频时长内均分，保证不出现
   0 时间戳，但会标记为估算，便于上层识别。

已给出且合法的时间锚点（``0 < start < end <= duration``）原样保留，不覆盖模型结论；
相邻段的起止做单调性校正，避免时间轴倒挂/重叠。
"""
from __future__ import annotations

import re
from typing import Any

_TS_LINE = re.compile(
    r"^(?:#\d+\s*)?\[(\d+):(\d+(?:\.\d+)?)-(\d+):(\d+(?:\.\d+)?)\]\s*(.*)$"
)
_KEEP = re.compile(r"[^\w\u4e00-\u9fff]+")


class Line:
    __slots__ = ("start_ms", "end_ms", "text")

    def __init__(self, start_ms: int, end_ms: int, text: str) -> None:
        self.start_ms = start_ms
        self.end_ms = end_ms
        self.text = text

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"<Line {self.start_ms}-{self.end_ms} {self.text[:12]!r}>"


def _to_ms(mm: str, ss: str) -> int:
    return int(round((int(mm) * 60 + float(ss)) * 1000))


def parse_timestamped_lines(subtitle_text: str) -> list[Line]:
    """解析逐句 ``[mm:ss.s-mm:ss.s] 原话`` 文本；无时间戳的行忽略。"""
    lines: list[Line] = []
    for raw in (subtitle_text or "").splitlines():
        m = _TS_LINE.match(raw.strip())
        if not m:
            continue
        start, end = _to_ms(m.group(1), m.group(2)), _to_ms(m.group(3), m.group(4))
        text = m.group(5).strip()
        if end <= start:
            end = start + 1000
        lines.append(Line(start, end, text))
    return lines


def number_lines(subtitle_text: str) -> str:
    """给带时间戳的逐句文本加行号（``#01``），便于模型直接引用行号区间。"""
    out: list[str] = []
    n = 0
    for raw in (subtitle_text or "").splitlines():
        if _TS_LINE.match(raw.strip()):
            n += 1
            out.append(f"#{n:02d} {raw.strip()}")
        elif raw.strip():
            out.append(raw)
    return "\n".join(out)


def _norm(text: str) -> str:
    return _KEEP.sub("", text or "")


def _longest_common_substring_len(a: str, b: str) -> int:
    """返回 a、b 的最长公共子串长度（滚动数组，输入为短文，性能足够）。"""
    if not a or not b:
        return 0
    prev = [0] * (len(b) + 1)
    best = 0
    for i in range(1, len(a) + 1):
        cur = [0] * (len(b) + 1)
        ai = a[i - 1]
        for j in range(1, len(b) + 1):
            if ai == b[j - 1]:
                cur[j] = prev[j - 1] + 1
                if cur[j] > best:
                    best = cur[j]
        prev = cur
    return best


def _candidate_texts(seg: dict) -> list[str]:
    keys = ("quote", "quotes", "original_text", "source_text", "raw_text", "原文", "原话")
    texts: list[str] = []
    for key in keys:
        val = seg.get(key)
        if isinstance(val, str) and val.strip():
            texts.append(val.strip())
        elif isinstance(val, list):
            texts.extend(str(v).strip() for v in val if str(v).strip())
    # 摘要/标题作为兜底（多数是改写，命中率低但成本近乎零）
    for key in ("summary", "title"):
        val = seg.get(key)
        if isinstance(val, str) and val.strip():
            texts.append(val.strip())
    return texts


def _match_range(seg: dict, lines: list[Line]) -> tuple[int, int] | None:
    """用段落文本在转写行里找连续命中区间，返回 (start_ms, end_ms)。"""
    best: tuple[int, int, int] | None = None  # (score, i, j)
    for text in _candidate_texts(seg):
        needle = _norm(text)
        if len(needle) < 6:
            continue
        hits = [i for i, ln in enumerate(lines) if needle[:60] and needle[:60] in _norm(ln.text)]
        if not hits:
            # 退一步：找最长公共子串最长的那一行
            scores = [(_longest_common_substring_len(needle, _norm(ln.text)), i) for i, ln in enumerate(lines)]
            score, idx = max(scores, default=(0, -1))
            if idx >= 0 and score >= 10:
                hits = [idx]
        if hits:
            cand = (len(needle), hits[0], hits[-1])
            if best is None or cand[0] > best[0]:
                best = cand
    if best is None:
        return None
    _, i, j = best
    return lines[i].start_ms, lines[j].end_ms


def _valid(seg: dict, duration_ms: int) -> bool:
    try:
        start, end = int(seg.get("start_ms") or 0), int(seg.get("end_ms") or 0)
    except (TypeError, ValueError):
        return False
    return 0 < start < end and (duration_ms <= 0 or end <= duration_ms + 2000)


def _lines_hint(seg: dict, lines: list[Line]) -> tuple[int, int] | None:
    """按模型给出的 lines 行号区间（1-based 闭区间）换算时间。"""
    raw = seg.get("lines") or seg.get("line_range")
    if isinstance(raw, str):
        nums = [int(x) for x in re.findall(r"\d+", raw)]
    elif isinstance(raw, (list, tuple)):
        nums = [int(x) for x in raw if str(x).strip().lstrip("-").isdigit()]
    else:
        return None
    if not nums:
        return None
    i, j = min(nums), max(nums)
    if not (1 <= i <= len(lines) and 1 <= j <= len(lines)):
        return None
    return lines[i - 1].start_ms, lines[j - 1].end_ms


def anchor_segments(
    content: dict[str, Any],
    subtitle_text: str,
    duration_ms: int = 0,
) -> dict[str, int]:
    """就地为 L3 content 的 segments 回填 start_ms/end_ms，返回统计。"""
    segs = content.get("segments") or []
    if not isinstance(segs, list) or not segs:
        return {}

    lines = parse_timestamped_lines(subtitle_text)
    stats = {"kept": 0, "by_lines": 0, "by_match": 0, "estimated": 0}
    cursor = 0  # 单调性校正用：上一段结束时间

    for idx, seg in enumerate(segs):
        if not isinstance(seg, dict):
            continue
        if _valid(seg, duration_ms):
            stats["kept"] += 1
            start, end = int(seg["start_ms"]), int(seg["end_ms"])
        else:
            found = _lines_hint(seg, lines) if lines else None
            src = "by_lines"
            if found is None:
                found = _match_range(seg, lines) if lines else None
                src = "by_match"
            if found is None:
                span = duration_ms if duration_ms > 0 else (lines[-1].end_ms if lines else 0)
                share = span / len(segs)
                start, end = int(idx * share), int((idx + 1) * share)
                src = "estimated"
            else:
                start, end = found
            stats[src] += 1

        # 单调性：不得早于上一段结束；为 0 的锚点视为无效，退回估算
        if end <= start:
            end = start + 1000
        if start < cursor:
            start = cursor
        if end <= start:
            end = start + 1000
        seg["start_ms"], seg["end_ms"] = int(start), int(end)
        cursor = end

    return stats
