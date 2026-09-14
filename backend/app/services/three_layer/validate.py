"""第 7.2 节落库校验：结构层硬拦截（R1~R9、R14）+ 语义层降级评审（R10~R13）。

处置口径严格按文档：
- ``硬拦截``：该条不写库（并由调用方记录 dropped 原因）；
- ``降级``：照常写库，但 ``status='draft'``、扣分并进评审队列；
- ``质量分``：``quality_score = 100 − 15 × 降级项数``，``< 60`` 恒为 draft。

R3（时间自洽）在文档中的处置是"以原料为准**修正**后入库"，因此本模块提供
``correct_time`` 供写入前调用；若仍有无法修正的违规（end < start 等）才升级为硬拦截。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any, Iterable, Sequence

# ---------------- 词表（与 lib_tag 中 dimension='function_label' 一致，作为兜底） ----------------

FUNCTION_LABELS: tuple[str, ...] = (
    "钩子", "铺垫", "冲突", "转折", "高潮", "干货", "CTA", "开头", "结尾", "引入",
    "介绍", "总结", "升华", "情绪", "悬念", "反差", "冲突点", "记忆点", "价值点",
    "共鸣", "互动",
)

FEELING_WORDS: tuple[str, ...] = (
    "很抓人", "有共鸣", "节奏感强", "制作精良", "情绪到位", "值得学习", "很震撼",
    "很精彩", "引人入胜", "令人印象深刻", "很上头", "拍得好",
)

# 受控白名单兜底（首选从 lib_tag 读取，见 load_whitelists）
ENUM_FALLBACK: dict[str, set[str]] = {
    "seg_type": {"钩子", "铺垫", "冲突", "转折", "高潮", "干货", "CTA"},
    "sentence_function": {"钩子", "铺垫", "冲突", "转折", "高潮", "干货", "CTA", "过渡", "收尾"},
    "shape": {"单峰", "双峰", "递进上升", "波浪", "骤升缓降", "前高后低", "平缓"},
    "turn_type": {"峰", "谷", "反转", "悬念"},
    "slot_role": {"固定", "可替换"},
    "evidence_type": {"transcript", "ocr", "frame", "audio"},
    "value_type": {"实用", "情绪", "娱乐"},
    "hook_position": {"前3秒", "片中", "结尾"},
    "abstraction_level": {"句法级", "段落级", "全片级"},
    "intent": {"涨粉", "带货", "种草", "引流", "科普", "情绪共鸣"},
    "copy_type": {"口播", "字幕", "标题", "CTA", "封面文案"},
    "topic_type": {"痛点型", "好奇型", "利益型", "身份型", "反常识型"},
    "method_category": {"叙事", "结构", "修辞", "视听", "节奏", "互动", "运营"},
    "rhetoric": {"设问", "排比", "对比", "夸张", "比喻", "反问", "递进", "白描", "数字锚定"},
}

_REQUIRED: dict[str, tuple[str, ...]] = {
    "script_script": ("core_idea", "content_trend", "target_audience", "summary"),
    "script_segment": ("seg_type", "purpose", "summary", "start_sentence_seq", "end_sentence_seq"),
    "script_sentence": (
        "quote", "sentence_function", "function_reason", "emotion_intensity",
        "variants", "imagination",
    ),
    "script_evidence": ("evidence_type", "quote"),
    "script_turn_point": ("turn_type", "t_ms", "intensity", "note"),
    "lib_topic": ("name", "audience", "pain_point", "angle", "mechanism", "variants", "imagination"),
    "lib_hook": ("name", "hook_type", "sentence_pattern", "mechanism", "variants", "imagination"),
    "lib_copywriting": ("name", "copy_type", "sentence_pattern", "mechanism", "variants", "imagination"),
    "lib_quote": ("text", "structure", "rewrite_template", "mechanism", "variants", "imagination"),
    "lib_method": ("name", "category", "mechanism", "usage_steps", "counter_example", "variants", "imagination"),
    "lib_combo": (
        "intent", "core_idea_alignment", "content_trend", "sequence_desc",
        "emotion_shape", "mechanism", "variants", "imagination",
    ),
}

_ENUM_FIELDS: dict[str, tuple[str, ...]] = {
    "script_segment": ("seg_type",),
    "script_sentence": ("sentence_function",),
    "script_evidence": ("evidence_type",),
    "script_turn_point": ("turn_type",),
    "lib_topic": ("value_type",),
    "lib_hook": ("position", "hook_type"),
    "lib_copywriting": ("copy_type",),
    "lib_method": ("category", "abstraction_level"),
    "lib_combo": ("intent", "emotion_shape"),
    "lib_combo_slot": ("slot_role",),
}

_VERB_HINTS = ("先", "再", "然后", "用", "把", "让", "切", "换", "写", "给", "放", "拆", "补", "压", "留", "接")

_CODE_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*){2}$")
_HEX32_RE = re.compile(r"^[0-9a-f]{32}$")


def load_whitelists(db_rows: Iterable[tuple[str, str]]) -> dict[str, set[str]]:
    """从 ``lib_tag`` 行 (code, dimension) 构造白名单：``dimension`` → 取值集合。

    ``code`` 形如 ``seg_type.钩子``；未登记的 dimension 回落兜底表。
    """
    out: dict[str, set[str]] = {k: set(v) for k, v in ENUM_FALLBACK.items()}
    for code, dimension in db_rows:
        if not code or not dimension or "." not in code:
            continue
        value = code.split(".", 1)[1]
        out.setdefault(dimension, set()).add(value)
    return out


# ---------------- 文本工具 ----------------

def char_ngrams(text: str, n: int = 3) -> set[str]:
    t = re.sub(r"\s+", "", text or "")
    if len(t) < n:
        return {t} if t else set()
    return {t[i : i + n] for i in range(len(t) - n + 1)}


def jaccard(a: str, b: str, n: int = 3) -> float:
    ga, gb = char_ngrams(a, n), char_ngrams(b, n)
    if not ga or not gb:
        return 0.0
    return len(ga & gb) / len(ga | gb)


def similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, re.sub(r"\s+", "", a or ""), re.sub(r"\s+", "", b or "")).ratio()


# R8 逐字比对前先做"去噪规范化"：只丢空白与标点，保留全部实义字符（含数字、
# 英文与别字）。ASR 原文本身没有标点，模型产出常自带标点，若不去噪会把
# "剧情、多人、士兵模式" 与 "剧情多人士兵模式" 误判成不一致。
_QUOTE_NOISE = set(" \t\r\n　，。、！？：；、０“”‘’（）()《》〈〉「」『』,.!?;:-—－～~…·|丨[]【】{}<>\"'")


def normalize_quote(text: str) -> str:
    """R8 去噪：剔除空白与标点，仅保留实义字符。"""
    return "".join(ch for ch in (text or "") if ch not in _QUOTE_NOISE)


def label_coverage(text: str, words: Sequence[str] = FUNCTION_LABELS) -> float:
    """文本中命中功能标签词表的字符占比。"""
    t = re.sub(r"\s+", "", text or "")
    if not t:
        return 1.0
    hit = [False] * len(t)
    for w in words:
        start = 0
        while True:
            i = t.find(w, start)
            if i < 0:
                break
            for k in range(i, min(len(t), i + len(w))):
                hit[k] = True
            start = i + 1
    return sum(hit) / len(t)


def is_label_only(text: str) -> bool:
    """R11：整段由功能标签构成（或 <20 字且 90% 字符命中词表）→ 视为功能标签。"""
    t = (text or "").strip()
    if not t:
        return True
    if t in FUNCTION_LABELS:
        return True
    if len(t) < 20 and label_coverage(t) >= 0.9:
        return True
    return False


def has_feeling_word(text: str) -> str | None:
    t = text or ""
    for w in FEELING_WORDS:
        if w in t:
            return w
    return None


def is_code(text: str) -> bool:
    return bool(_CODE_RE.match((text or "").strip()))


# ---------------- 判定结果 ----------------

@dataclass
class Verdict:
    row_type: str
    label: str
    hard: list[str] = field(default_factory=list)
    soft: list[str] = field(default_factory=list)
    corrected: list[str] = field(default_factory=list)

    @property
    def downgrade_count(self) -> int:
        return len(self.soft)

    @property
    def quality_score(self) -> float:
        return max(0.0, 100.0 - 15.0 * self.downgrade_count)

    @property
    def status(self) -> str:
        if self.hard:
            return "rejected"
        return "active" if self.quality_score >= 60 else "draft"

    @property
    def accepted(self) -> bool:
        return not self.hard

    def as_dict(self) -> dict[str, Any]:
        return {
            "row_type": self.row_type,
            "label": self.label,
            "hard": self.hard,
            "soft": self.soft,
            "corrected": self.corrected,
            "quality_score": self.quality_score,
            "status": self.status,
        }


def _empty(v: Any) -> bool:
    if v is None:
        return True
    if isinstance(v, str):
        return not v.strip()
    if isinstance(v, (list, tuple, dict, set)):
        return len(v) == 0
    return False


def correct_time(start_ms: int, end_ms: int, source: tuple[int, int] | None) -> tuple[int, int, bool]:
    """R3 修正：以原料（``raw_transcript_sentence``）时间为准；无原料时保证自洽。"""
    s = max(0, int(start_ms or 0))
    e = max(0, int(end_ms or 0))
    changed = False
    if source is not None:
        rs, re_ = int(source[0] or 0), int(source[1] or 0)
        if abs(s - rs) > 300 or abs(e - re_) > 300:
            s, e, changed = max(0, rs), max(0, re_), True
    if e == 0:
        e = s
        changed = True
    if e < s:
        s, e, changed = min(s, e), max(s, e), True
    return s, e, changed


class Validator:
    """携带上下文（片长、原料句、画面描述、白名单）的校验器。"""

    def __init__(
        self,
        *,
        duration_ms: int,
        raw_by_seq: dict[int, dict[str, Any]] | None = None,
        raw_by_id: dict[str, dict[str, Any]] | None = None,
        raw_chain: Sequence[dict[str, Any]] = (),
        shot_descs: Sequence[str] = (),
        whitelists: dict[str, set[str]] | None = None,
    ) -> None:
        self.duration_ms = max(0, int(duration_ms or 0))
        self.raw_by_seq = raw_by_seq or {}
        self.raw_by_id = raw_by_id or {}
        self.shot_descs = list(shot_descs or [])
        self.wl = {k: set(v) for k, v in ENUM_FALLBACK.items()}
        if whitelists:
            for k, v in whitelists.items():
                self.wl[k] = set(v)

        # R8 逐字链：raw_transcript_sentence 按 seq 升序拼接的"整片原始口播"。
        # quote 允许跨句拼接，因此判定的基准不是单条 raw 句，而是这条链上的连续子串。
        self.raw_chain: list[dict[str, Any]] = [dict(r) for r in (raw_chain or ())]
        self.chain_norm = normalize_quote("".join(str(r.get("text") or "") for r in self.raw_chain))
        self.chain_spans: list[tuple[str, int, int, int]] = []  # (raw_id, raw_seq, start_off, end_off)
        _off = 0
        for r in self.raw_chain:
            _len = len(normalize_quote(str(r.get("text") or "")))
            self.chain_spans.append((str(r.get("id") or ""), int(r.get("seq") or 0), _off, _off + _len))
            _off += _len

    # ---- R8 原料链定位 ----

    def quote_span(self, quote: str) -> dict[str, Any] | None:
        """把 quote 定位到原料链上：返回其覆盖的 raw 句区间；非连续子串返回 None。

        跨句拼接（如 ASR 断句导致 quote 覆盖 raw 1~2 或 raw 25~26）会正常返回，
        且 ``span > 1`` 即表示该 quote 由多条原料句拼接而成。
        """
        qn = normalize_quote(quote)
        if not qn or not self.chain_norm:
            return None
        pos = self.chain_norm.find(qn)
        if pos < 0:
            return None
        end = pos + len(qn)
        covered = [sp for sp in self.chain_spans if sp[2] < end and sp[3] > pos]
        if not covered:
            return None
        return {
            "pos": pos,
            "end": end,
            "span": len(covered),
            "raw_ids": [sp[0] for sp in covered],
            "raw_seqs": [sp[1] for sp in covered],
            "first_raw_id": covered[0][0],
            "last_raw_id": covered[-1][0],
        }

    def _anchor_chain_index(self, ids: Sequence[str]) -> int | None:
        wanted = {str(x) for x in (ids or ()) if str(x)}
        if not wanted:
            return None
        for i, sp in enumerate(self.chain_spans):
            if sp[0] in wanted:
                return i
        return None

    def _quote_window(self, quote: str, ids: Sequence[str] = (), raw_text: str | None = None) -> str:
        """锚点邻域窗口：从锚点 raw 句起向后拼接、必要时向前补，供相似度兜底比对。"""
        need = len(normalize_quote(quote))
        idx = self._anchor_chain_index(ids)
        if idx is None or not self.chain_spans:
            return str(raw_text or self.chain_norm)
        parts: list[str] = []
        total = 0
        for i in range(idx, len(self.chain_spans)):
            parts.append(str(self.raw_chain[i].get("text") or ""))
            total += self.chain_spans[i][3] - self.chain_spans[i][2]
            if total >= need:
                break
        if total < need:
            for i in range(idx - 1, -1, -1):
                parts.insert(0, str(self.raw_chain[i].get("text") or ""))
                total += self.chain_spans[i][3] - self.chain_spans[i][2]
                if total >= need:
                    break
        return "".join(parts)

    # ---- 单规则 ----

    def r1_required(self, row_type: str, row: dict[str, Any], v: Verdict) -> None:
        for field_name in _REQUIRED.get(row_type, ()):
            if _empty(row.get(field_name)):
                v.hard.append(f"R1 必填字段为空：{field_name}")

    def r2_enum(self, row_type: str, row: dict[str, Any], v: Verdict) -> None:
        for field_name in _ENUM_FIELDS.get(row_type, ()):
            value = row.get(field_name)
            if _empty(value):
                continue
            allowed = self.wl.get(field_name) or self.wl.get(f"{row_type}.{field_name}")
            if allowed and str(value) not in allowed:
                v.hard.append(f"R2 枚举越界：{field_name}={value}")

    def r3_time(self, row: dict[str, Any], v: Verdict, *, source: tuple[int, int] | None = None) -> None:
        s, e, changed = correct_time(row.get("start_ms") or 0, row.get("end_ms") or 0, source)
        if changed:
            v.corrected.append(f"R3 时间以原料为准修正：{row.get('start_ms')}/{row.get('end_ms')} → {s}/{e}")
        if e and s and e < s:
            v.hard.append("R3 时间不自洽：end_ms < start_ms")
        if self.duration_ms and e > self.duration_ms + 3000:
            v.soft.append(f"R3 时间越界：end_ms={e} > 片长 {self.duration_ms}")

    def r4_granularity(self, sentence_count: int, v: Verdict) -> None:
        if self.duration_ms <= 0:
            return
        lo, hi = self.sentence_range()
        if not (lo <= sentence_count <= hi):
            v.soft.append(f"R4 句数越界：{sentence_count} ∉ [{lo},{hi}]")

    def r5_code(self, row: dict[str, Any], v: Verdict, *, field_name: str = "code") -> None:
        value = str(row.get(field_name) or "")
        if value and not is_code(value):
            v.hard.append(f"R5 编码不规范：{field_name}={value}")

    def r6_triplet(self, row: dict[str, Any], v: Verdict) -> None:
        missing = [k for k in ("raw_sentence_id", "source_video_id") if _empty(row.get(k))]
        if _empty(row.get("start_ms")) and _empty(row.get("end_ms")):
            missing.append("start_ms/end_ms")
        if missing:
            v.hard.append(f"R6 三件套缺失：{'、'.join(missing)}")

    def r7_raw_ref(self, row: dict[str, Any], v: Verdict) -> None:
        rid = row.get("raw_sentence_id")
        if rid and self.raw_by_id and str(rid) not in self.raw_by_id:
            v.hard.append(f"R7 原料回指无效：raw_sentence_id={rid}")

    def r8_quote(
        self,
        row: dict[str, Any],
        v: Verdict,
        *,
        raw_text: str | None = None,
        anchor_ids: Sequence[str] = (),
    ) -> None:
        """R8 原话一致度（含跨句拼接口径）。

        判定链（自上而下，命中即停）：

        1. **省略标记**（``…`` / ``...``）→ 硬拦截（视为改写或节选）；
        2. **逐字命中**：quote 去噪后是"整片原料链"（``raw_transcript_sentence``
           按 seq 拼接）的连续子串 → 通过；跨句拼接合法，故 quote 覆盖多条 raw 句
           不算违规（记 corrected 供证据留痕），但回指锚点必须落在覆盖区间内，
           否则按"回指不实"硬拦截；
        3. **锚点邻域兜底**：与锚点起原料窗口的相似度 ≥ 0.9 → 通过；
        4. 其余 → 硬拦截（既非原料连续子串、相似度也不足 0.9，判为改写）。

        非 transcript 类证据（ocr / frame / audio）没有可逐字比对的原料，直接跳过。
        """
        quote = str(row.get("quote") or "")
        if not quote.strip():
            return
        ev_type = str(row.get("evidence_type") or "transcript").lower()
        if ev_type != "transcript":
            return
        if "…" in quote or "..." in quote:
            v.hard.append("R8 原话含省略标记（视为改写）")
            return

        anchor = [str(x) for x in (anchor_ids or ()) if str(x)]
        if not anchor and row.get("raw_sentence_id"):
            anchor = [str(row["raw_sentence_id"])]

        span = self.quote_span(quote)
        if span is not None:
            if span["span"] > 1:
                v.corrected.append(
                    f"R8 跨句拼接：quote 覆盖 raw #{span['raw_seqs'][0]}~#{span['raw_seqs'][-1]}"
                    f"（{span['span']} 条原料句，ASR 断句口径）"
                )
            if anchor and not (set(anchor) & set(span["raw_ids"])):
                v.hard.append(
                    "R8 回指不实：quote 在原料链上的位置（raw #"
                    f"{span['raw_seqs'][0]}）不在回指锚点覆盖范围内"
                )
                return
            return

        expected = anchor or [str(x) for x in (self._anchor_ids_by_seq(row) or ())]
        window = self._quote_window(quote, expected, raw_text=raw_text)
        sim = similarity(normalize_quote(quote), normalize_quote(window))
        if sim >= 0.9:
            v.corrected.append(f"R8 以锚点邻域兜底通过：相似度 {round(sim, 3)}")
            return
        v.hard.append(
            f"R8 原话一致度不足：{round(sim, 3)} < 0.9（既非原料连续子串，也非锚点邻域原话，判为改写）"
        )

    def _anchor_ids_by_seq(self, row: dict[str, Any]) -> list[str]:
        """按行号（合并后 seq）取出该单元覆盖的全部原料句 id。"""
        src = self.raw_by_seq.get(int(row.get("line_no") or 0))
        if not src:
            return []
        ids = src.get("raw_ids")
        if isinstance(ids, (list, tuple)) and ids:
            return [str(x) for x in ids]
        return [str(src.get("id"))] if src.get("id") else []

    def r9_refs(self, refs: Sequence[dict[str, Any]], v: Verdict) -> None:
        if not refs:
            v.hard.append("R9 缺少溯源记录（ref_element_source）")
            return
        for r in refs:
            if _empty(r.get("quote")) or _empty(r.get("start_ms")) and _empty(r.get("end_ms")):
                v.hard.append("R9 溯源记录缺 quote/时间锚")

    def r10_scene_copy(self, row: dict[str, Any], v: Verdict) -> None:
        text = " ".join(
            str(row.get(k) or "")
            for k in ("name", "mechanism", "usage_steps", "function_reason", "purpose", "expected_function")
        )
        if not text.strip():
            return
        worst = max((jaccard(text, d) for d in self.shot_descs), default=0.0)
        if worst > 0.35:
            v.soft.append(f"R10 疑似画面照搬：与 raw_shot.desc 3-gram Jaccard={round(worst, 3)} > 0.35")

    def r11_label(self, row: dict[str, Any], v: Verdict) -> None:
        text = row.get("function_reason") or row.get("mechanism") or row.get("usage_steps") or ""
        if not str(text).strip():
            # 段落等行的解释性字段为 purpose/summary；空值交给 R1 处置，避免误报
            text = row.get("purpose") or row.get("summary") or row.get("expected_function") or ""
        if str(text).strip() and is_label_only(str(text)):
            v.hard.append("R11 功能标签式解释（整段为分类名或 <20 字且 90% 命中标签词表）")
        for key in ("function_reason", "mechanism", "usage_steps", "purpose", "expected_function"):
            hit = has_feeling_word(str(row.get(key) or ""))
            if hit:
                v.soft.append(f"R11 出现观后感用词：{hit}（字段 {key}）")

    def r12_divergence(self, row: dict[str, Any], v: Verdict) -> None:
        variants = row.get("variants") or []
        if not isinstance(variants, list) or len(variants) < 2:
            v.soft.append(f"R12 变体不足：{len(variants) if isinstance(variants, list) else 0} < 2")
        elif isinstance(variants, list):
            texts = [str(x.get("text") if isinstance(x, dict) else x) for x in variants]
            for i in range(len(texts)):
                for j in range(i + 1, len(texts)):
                    if similarity(texts[i], texts[j]) >= 0.8:
                        v.soft.append(f"R12 变体重复：第 {i + 1}/{j + 1} 条相似度 ≥ 0.8")
        imagination = str(row.get("imagination") or "")
        if len(imagination) < 30:
            v.soft.append(f"R12 发散不足：imagination {len(imagination)} 字 < 30")

    def r13_mechanism(self, row: dict[str, Any], v: Verdict) -> None:
        steps = str(row.get("usage_steps") or "")
        if len(steps) < 30:
            v.soft.append(f"R13 操作步骤过短：{len(steps)} 字 < 30")
        elif not any(h in steps for h in _VERB_HINTS):
            v.soft.append("R13 操作步骤缺明确动词短语")
        if _empty(row.get("counter_example")):
            v.soft.append("R13 缺反例 counter_example")

    def r14_combo(self, combo: dict[str, Any], slots: Sequence[dict[str, Any]], v: Verdict) -> None:
        if not slots:
            v.hard.append("R14 组合无槽位")
            return
        ordered = sorted(slots, key=lambda s: float(s.get("position_ratio_start") or 0))
        if abs(float(ordered[0].get("position_ratio_start") or 0)) > 1e-6:
            v.hard.append("R14 槽位未从 0 开始")
        if abs(float(ordered[-1].get("position_ratio_end") or 0) - 1.0) > 1e-6:
            v.hard.append("R14 槽位未覆盖到 1.0")
        for a, b in zip(ordered, ordered[1:]):
            if abs(float(a.get("position_ratio_end") or 0) - float(b.get("position_ratio_start") or 0)) > 1e-6:
                v.hard.append("R14 槽位区间有缝隙或重叠")
                break
        fixed = sum(1 for s in slots if s.get("slot_role") == "固定")
        swap = sum(1 for s in slots if s.get("slot_role") == "可替换")
        if fixed + swap != len(slots):
            v.hard.append("R14 槽位角色取值非法（fixed + swap ≠ slot_count）")
        curve = combo.get("emotion_curve") or []
        if len(curve) != len(slots):
            v.hard.append(f"R14 曲线点数 {len(curve)} ≠ 槽位数 {len(slots)}")

    # ---- 组合判定 ----

    def validate(
        self,
        row_type: str,
        row: dict[str, Any],
        *,
        label: str = "",
        refs: Sequence[dict[str, Any]] = (),
        raw_text: str | None = None,
        anchor_ids: Sequence[str] = (),
    ) -> Verdict:
        v = Verdict(row_type=row_type, label=label or str(row.get("code") or row.get("name") or ""))
        if row_type == "script_segment":
            self.r1_required(row_type, row, v)
            self.r2_enum(row_type, row, v)
            self.r3_time(row, v)
            self.r11_label(row, v)
            self.r10_scene_copy(row, v)
        elif row_type == "script_sentence":
            self.r1_required(row_type, row, v)
            self.r2_enum(row_type, row, v)
            self.r6_triplet(row, v)
            self.r7_raw_ref(row, v)
            self.r8_quote(row, v, raw_text=raw_text, anchor_ids=anchor_ids)
            self.r11_label(row, v)
            self.r12_divergence(row, v)
            if not (0 <= float(row.get("emotion_intensity") or 0) <= 10):
                v.hard.append("R2 情绪强度越界：emotion_intensity ∉ [0,10]")
            if len(str(row.get("quote") or "")) < 8:
                v.soft.append("R2 原话偏短：quote < 8 字（请核对是否为完整口播行）")
        elif row_type == "script_evidence":
            self.r1_required(row_type, row, v)
            self.r2_enum(row_type, row, v)
            self.r3_time(row, v)
            self.r8_quote(row, v, raw_text=raw_text, anchor_ids=anchor_ids)
        elif row_type == "script_turn_point":
            self.r1_required(row_type, row, v)
            self.r2_enum(row_type, row, v)
        elif row_type == "lib_combo":
            self.r1_required(row_type, row, v)
            self.r2_enum(row_type, row, v)
            self.r10_scene_copy(row, v)
            self.r11_label(row, v)
            self.r12_divergence(row, v)
            self.r9_refs(refs, v)
        elif row_type == "lib_combo_slot":
            self.r2_enum(row_type, row, v)
        elif row_type.startswith("lib_"):
            self.r1_required(row_type, row, v)
            self.r2_enum(row_type, row, v)
            self.r5_code(row, v)
            self.r10_scene_copy(row, v)
            self.r11_label(row, v)
            self.r12_divergence(row, v)
            if row_type == "lib_method":
                self.r13_mechanism(row, v)
            self.r9_refs(refs, v)
        elif row_type == "script_script":
            self.r1_required(row_type, row, v)
            self.r3_time({"start_ms": 0, "end_ms": row.get("duration_ms") or 0}, v)
        return v

    # ---- 汇总 ----

    def sentence_range(self) -> tuple[int, int]:
        import math

        d = self.duration_ms
        lo = math.ceil(d / 4000.0)
        hi = math.floor(d / 2400.0)
        return max(1, lo), max(1, hi)

    def curve_summary(self, values: Sequence[float]) -> dict[str, Any]:
        from . import emotion

        return emotion.analysis(values, list(range(0, len(values) * 500, 500))[: len(values)])


def score_rows(verdicts: Sequence[Verdict]) -> dict[str, Any]:
    """汇总一批宿主的校验结果，供证据报告使用。"""
    total = len(verdicts)
    rejected = [v for v in verdicts if not v.accepted]
    drafted = [v for v in verdicts if v.accepted and v.status == "draft"]
    soft_total = sum(len(v.soft) for v in verdicts)
    return {
        "total": total,
        "rejected": len(rejected),
        "draft": len(drafted),
        "active": total - len(rejected) - len(drafted),
        "soft_total": soft_total,
        "rejected_rows": [v.as_dict() for v in rejected],
        "draft_rows": [v.as_dict() for v in drafted],
    }


# ---------------------------------------------------------------- 元素质量基线验收

ELEMENT_BASELINE_RULE = "hard=0 且 观后感/变体/机制 soft 率 ≤ 25%"
ELEMENT_BASELINE_SOFT_LIMIT = 0.25


def element_baseline(verdicts: Sequence[dict]) -> dict:
    """元素质量基线验收（批量判定）：一条拆解产出的积木元素整体是否达标。

    口径（用户偏好，Marvis 知识库 preference）：
    - 落库元素必须是从视频提炼的"方法论/表现手法"，而非画面描述或观后感；
    - 必须带证据链（原话 + 秒数）、机制（为什么有效）、变体（发散迁移方向 2~3 个）；
    - 证据链与结构严校验（hard 违规 = 0），创意可发散（soft 率 ≤ 25% 达标）。

    输入为 pipeline 的 verdicts（as_dict 后的列表，含 row_type/hard/soft/label）。
    仅统计 lib_* 元素行（不含 script_* 行）。
    """
    rows = [v for v in verdicts if str(v.get("row_type") or "").startswith("lib_")]
    out: dict[str, Any] = {
        "rule": ELEMENT_BASELINE_RULE,
        "total": len(rows),
        "by_type": {},
        "hard_rows": [],
        "label_rows": [],
        "variant_rows": [],
        "mechanism_rows": [],
        "evidence_rows": [],
        "rates": {},
        "pass": None,
        "reason": "",
    }
    if not rows:
        out["pass"] = False
        out["reason"] = "无元素产出（L5 未返回可用积木）"
        return out
    for v in rows:
        rt = str(v.get("row_type") or "")
        out["by_type"][rt] = out["by_type"].get(rt, 0) + 1
        hard = [str(m) for m in (v.get("hard") or [])]
        soft = [str(m) for m in (v.get("soft") or [])]
        item = {"label": str(v.get("label") or ""), "row_type": rt}
        if hard:
            out["hard_rows"].append({**item, "issues": hard[:4]})
        if any(m.startswith("R11") for m in hard + soft):
            out["label_rows"].append({**item, "issues": [m for m in hard + soft if m.startswith("R11")][:2]})
        if any(m.startswith("R12") for m in soft):
            out["variant_rows"].append({**item, "issues": [m for m in soft if m.startswith("R12")][:2]})
        if any(m.startswith("R13") for m in soft):
            out["mechanism_rows"].append({**item, "issues": [m for m in soft if m.startswith("R13")][:2]})
        if any(m.startswith("R9") for m in hard):
            out["evidence_rows"].append({**item, "issues": [m for m in hard if m.startswith("R9")][:2]})

    n = max(1, len(rows))
    rates = {
        "hard": round(len(out["hard_rows"]) / n, 4),
        "label": round(len(out["label_rows"]) / n, 4),
        "variant": round(len(out["variant_rows"]) / n, 4),
        "mechanism": round(len(out["mechanism_rows"]) / n, 4),
        "evidence": round(len(out["evidence_rows"]) / n, 4),
    }
    out["rates"] = rates
    out["pass"] = (
        len(out["hard_rows"]) == 0
        and rates["label"] <= ELEMENT_BASELINE_SOFT_LIMIT
        and rates["variant"] <= ELEMENT_BASELINE_SOFT_LIMIT
        and rates["mechanism"] <= ELEMENT_BASELINE_SOFT_LIMIT
    )
    if out["pass"]:
        out["reason"] = "通过：无 hard 违规，观后感/变体/机制 soft 率均在 25% 内"
    else:
        reasons = []
        if len(out["hard_rows"]):
            reasons.append(f"hard 违规 {len(out['hard_rows'])} 条")
        for k, label in (("label", "观后感"), ("variant", "变体"), ("mechanism", "机制")):
            if rates[k] > ELEMENT_BASELINE_SOFT_LIMIT:
                reasons.append(f"{label} soft 率 {rates[k]:.0%} > 25%")
        out["reason"] = "未通过：" + "；".join(reasons)
    return out
