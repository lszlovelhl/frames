"""faster-whisper 全量语音转写（ASR），输出带时间戳的分句文本。

统一用 ASR 拿全文案（不再依赖平台字幕），保证多平台口径一致。
模型加载优先级：
1. 环境变量 WHISPER_MODEL_PATH：本地 ctranslate2 模型目录（离线/镜像下载路径）；
2. 环境变量 WHISPER_MODEL（默认 medium）：HF 模型名，首次自动下载。
中文推荐 small / medium / large-v3（small 已可满足拆解文案用途且 CPU 快）。
"""
import asyncio
import difflib
import json
import logging
import os
import re
import subprocess
import time
from pathlib import Path

from app.services.llm_json import parse_json_loose
from app.services.zh import to_simplified

from app.media import ffbin

logger = logging.getLogger(__name__)

MODEL_SPEC = os.getenv("WHISPER_MODEL_PATH") or os.getenv("WHISPER_MODEL", "medium")

_model_cache: dict = {}


def _ensure_wav16k(video_path: str, work_dir: Path) -> Path:
    """转 16k 单声道 wav，faster-whisper 稳定读取且省解码。"""
    wav = work_dir / "audio_16k.wav"
    if wav.exists():
        return wav
    subprocess.run(
        [
            ffbin.ffmpeg_bin(), "-y", "-i", video_path, "-vn",
            "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(wav),
        ],
        check=True,
        capture_output=True,
    )
    return wav


def _get_model(spec: str):
    from faster_whisper import WhisperModel

    if spec not in _model_cache:
        logger.info("加载 faster-whisper 模型 %s ...", spec)
        t0 = time.time()
        _model_cache[spec] = WhisperModel(spec, device="cpu", compute_type="int8")
        logger.info("whisper %s 就绪，耗时 %.1fs", spec, time.time() - t0)
    return _model_cache[spec]


# ASR 管线版本：解码策略/清洗规则/校对策略变更时递增。
# services/media_prep.ensure_media 用它比对旧素材，版本落后则强制重新转写
# （否则「复用已有素材」短路会让新策略对老链接永远不生效）。
# v4：逐句时间戳注入下游（subtitle_text 带 [起-止]）+ 分批上下文纠错
ASR_PIPELINE_VERSION = 4


def format_ts(seconds: float) -> str:
    """秒 → ``mm:ss.s``（逐句时间锚点的展示格式）。"""
    try:
        sec = max(0.0, float(seconds or 0.0))
    except (TypeError, ValueError):
        sec = 0.0
    m, s = divmod(sec, 60)
    return f"{int(m):02d}:{s:04.1f}"


# 逐句文本行格式：[00:03.2-00:06.8] 句子内容
_TS_LINE = re.compile(
    r"^\[\s*(\d{1,3}):(\d{2}(?:\.\d+)?)\s*-\s*(\d{1,3}):(\d{2}(?:\.\d+)?)\s*\]\s*(.*)$"
)


def segments_to_timestamped_text(segments: list[dict] | None) -> str:
    """把 ASR 分段渲染成逐句带起止时间戳的文本（下游按句落库的时间锚点）。

    形如 ``[00:03.2-00:06.8] 句子内容``，一段一行；空文本行跳过。
    """
    lines: list[str] = []
    for s in segments or []:
        text = str(s.get("text") or "").strip()
        if not text:
            continue
        lines.append(f"[{format_ts(s.get('start'))}-{format_ts(s.get('end'))}] {text}")
    return "\n".join(lines)


def parse_timestamped_text(text: str) -> list[dict]:
    """``segments_to_timestamped_text`` 的逆运算：供校验/复用旧素材时还原分段。"""
    out: list[dict] = []
    for raw in (text or "").splitlines():
        m = _TS_LINE.match(raw.strip())
        if not m:
            continue
        start = int(m.group(1)) * 60 + float(m.group(2))
        end = int(m.group(3)) * 60 + float(m.group(4))
        body = m.group(5).strip()
        if not body:
            continue
        out.append({"start": round(start, 3), "end": round(end, 3), "text": body})
    return out

# 提示词回声标记（后置检测兜底特征）：
# 历史事故——向 whisper 传 initial_prompt 引导简体后，部分音频上模型把引导语
# 整段复读（13 段全为「请使用简体中文转写。」），真实语音内容全部丢失。
# 现已不再向解码器传 initial_prompt（简繁统一改由 zh.to_simplified 负责），
# 这里保留标记检测：一旦整段文本命中引导语，即判定退化并回退到备用解码配置。
_PROMPT_ECHO_MARKERS = (
    "请使用简体中文转写",
    "以下是普通话短视频口播内容",
    "以下是普通话口播",
)

# 备用解码配置：(配置名, 是否启用 VAD)
# 先全量（关 VAD，MV/BGM 混音下人声不会被误滤），退化时再试开 VAD 的配置。
_DECODE_ATTEMPTS: tuple[tuple[str, bool], ...] = (("novad", False), ("vad", True))


def _decode(model, wav: str, vad_filter: bool) -> tuple[str, float, list[dict]]:
    """单次解码；刻意不传 initial_prompt，避免提示词回声。"""
    segs, info = model.transcribe(
        str(wav),
        vad_filter=vad_filter,
        beam_size=5,
        word_timestamps=False,
    )
    items = [
        {
            "start": round(float(s.start), 3),
            "end": round(float(s.end), 3),
            "text": s.text.strip(),
        }
        for s in segs
    ]
    return info.language, float(info.duration or 0.0), items


def degenerate_reason(items: list[dict], duration: float) -> str:
    """判断一次转写结果是否「退化」（需要换配置重试）；空串表示正常。

    三类退化：
    1. prompt_echo：整段文本命中提示词引导语（真实语音丢失，最严重）；
    2. repeated：同一句重复占多数段（>=3 段且占比 >=60%，典型解码复读）；
    3. too_short / too_sparse：总字数过少或字/秒过低，基本没拿到口播。
    仅用于「在多次尝试中选更优的一次」，绝不丢弃任何一次的真实结果。
    """
    texts = [t for t in ((s.get("text") or "").strip() for s in items) if t]
    if not texts:
        return "empty"
    joined = "".join(texts)
    for mk in _PROMPT_ECHO_MARKERS:
        if mk in joined:
            return f"prompt_echo:{mk}"
    if len(texts) >= 3:
        from collections import Counter

        top, cnt = Counter(texts).most_common(1)[0]
        if cnt >= 3 and cnt / len(texts) >= 0.6:
            return f"repeated:{cnt}/{len(texts)}|{top[:20]}"
    total = sum(len(t) for t in texts)
    if total < 10:
        return "too_short"
    if duration > 20 and total / duration < 0.15:
        return "too_sparse"
    return ""


def _cand_score(cand: dict) -> tuple[int, int]:
    """候选优选打分：未退化优先，其次总字数更多。"""
    return (0 if cand["degenerate"] else 1, len(cand["text"]))


async def transcribe(video_path: str, work_dir: Path) -> dict:
    """返回 {language, text, segments:[{start,end,text}], duration, ...}

    多配置解码 + 退化回退：逐个尝试 _DECODE_ATTEMPTS，第一个未退化的结果直接采用；
    全部退化时取「字数最多」的一次（宁可保留嘈杂文本，也不返回空/回声）。
    """
    t0 = time.time()
    wav = _ensure_wav16k(video_path, work_dir)
    model = _get_model(MODEL_SPEC)

    import asyncio

    loop = asyncio.get_running_loop()

    best: dict | None = None
    last_exc: Exception | None = None
    for name, vad in _DECODE_ATTEMPTS:
        try:
            language, duration, items = await loop.run_in_executor(
                None, lambda v=vad: _decode(model, str(wav), v)
            )
        except Exception as exc:  # noqa: BLE001 单个配置失败不影响其它配置
            logger.exception("ASR 解码异常（%s）", name)
            last_exc = exc
            continue

        items = [s for s in items if s["text"]]
        # 清洗 whisper 碎片幻觉（无缝短句合并/相邻重复去重/段内连读压缩/超限采样），
        # 必须在校对之前：减少喂给 LLM 的噪音与 token 成本
        items, clean_stats = clean_segments(items)
        # 简繁统一：whisper 中文输出可能混入繁体，这里统一转简体（源头一）
        trad_fixed = 0
        for s in items:
            fixed = to_simplified(s["text"])
            if fixed != s["text"]:
                s["text"] = fixed
                trad_fixed += 1
        text = "\n".join(s["text"] for s in items)
        reason = degenerate_reason(items, duration)
        logger.info(
            "ASR 解码[%s]: segs=%d chars=%d 简繁修正=%d 退化=%s",
            name, len(items), len(text), trad_fixed, reason or "否",
        )
        cand = {
            "decode": name,
            "language": language,
            "duration": duration,
            "items": items,
            "text": text,
            "clean_stats": clean_stats,
            "trad_fixed": trad_fixed,
            "degenerate": reason,
        }
        if best is None or _cand_score(cand) > _cand_score(best):
            best = cand
        if not reason:
            break

    if best is None:
        if last_exc is not None:
            raise last_exc
        return {
            "language": "", "text": "", "segments": [], "duration": 0.0,
            "model": MODEL_SPEC, "pipeline_version": ASR_PIPELINE_VERSION,
            "decode": None, "degenerate": "empty",
        }

    if best["trad_fixed"]:
        logger.info("ASR 简繁统一：修正 %d 段繁体文本", best["trad_fixed"])
    if best["clean_stats"].get("cleaned_chars"):
        logger.info(
            "ASR 清洗: %s",
            ", ".join(f"{k}={v}" for k, v in best["clean_stats"].items() if v),
        )
    logger.info(
        "ASR 完成: decode=%s lang=%s dur=%.0fs segs=%d chars=%d 退化=%s 耗时 %.0fs",
        best["decode"], best["language"], best["duration"], len(best["items"]),
        len(best["text"]), best["degenerate"] or "否", time.time() - t0,
    )
    return {
        "language": best["language"],
        "text": best["text"],
        "segments": best["items"],
        "duration": best["duration"],
        "model": MODEL_SPEC,
        # 版本与解码自检信息：供素材复用判定与拆解链路排障
        "pipeline_version": ASR_PIPELINE_VERSION,
        "decode": best["decode"],
        "degenerate": best["degenerate"],
    }


# 校对分批参数：单批行数/字符上限、上下文行数（上下文只读，仅帮模型判断同音词）
# 批不宜大：glm-4-flash 一档在长批里会漏改（实测 10 行只改 1 处，8 行能改全），
# 8 行 + 上下文的召回与成本更平衡。
_PROOFREAD_BATCH_LINES = 8
_PROOFREAD_BATCH_CHARS = 1200
_PROOFREAD_CONTEXT_LINES = 3
# 同词同改传播：第一轮已采纳的「错词→正确词」（等长替换）补齐到全文其它同词位置。
# 弱模型在长批里会漏改重复出现的同音词（如 宁感 改了 2 处漏 1 处），传播可确定性补齐。
_PROOFREAD_PROPAGATE = True
# 批次数上限：极端长文（>7 万字符）时防止把免费额度打满，超限只校前 N 批
_PROOFREAD_MAX_BATCHES = 60
_PROOFREAD_CONCURRENCY = 3

# 清洗后喂给下游(校对/拆解)的文案硬上限；超限按段均匀采样
_TRANSCRIPT_MAX_CHARS = 16000

# 无缝碎片合并条件：间隔极短 + 极短无标点句 + 合并不堆叠成超长行
_MERGE_MAX_GAP = 0.25
_MERGE_MAX_CHARS = 8
_MERGE_MAX_TOTAL = 40

_REPEAT_CHAR = re.compile(r"(.)\1{4,}")  # 同一字符连读 >=5
_REPEAT_PHRASE = re.compile(r"([\u4e00-\u9fffA-Za-z0-9]{2,6}?)(?:\s*\1){2,}")  # 短语连读 >=3
_END_PUNCT = ("。", "！", "？", "!", "?", "…", "~", "～")


def _compress_inner(text: str) -> str:
    """段内连读幻觉压缩：'谢谢谢谢谢谢'->'谢谢谢谢'；'哈哈哈哈哈哈'->'哈哈哈'。"""
    out = _REPEAT_CHAR.sub(lambda m: m.group(1) * 3, text)
    out = _REPEAT_PHRASE.sub(lambda m: m.group(1) * 2, out)
    return out


def clean_segments(segments: list[dict]) -> tuple[list[dict], dict]:
    """清洗 whisper 碎片转写：合并无缝短句、去相邻完全重复、压段内连读。

    返回 (segments, stats)；任一步骤都保守可逆，不删除任何有内容的独立长句。
    stats: {original, merged, deduped, compressed, cleaned_chars, dropped}
    """
    stats = {"original": len(segments), "merged": 0, "deduped": 0,
             "compressed": 0, "cleaned_chars": 0, "dropped": 0}
    if not segments:
        return segments, stats

    # 1) 相邻完全重复(<=15字)只留 1 条——多为音频事件/主播口头禅被重复转写
    dedup: list[dict] = []
    for s in segments:
        if dedup and len(s["text"]) <= 15 and s["text"] == dedup[-1]["text"]:
            stats["deduped"] += 1
            continue
        dedup.append(s)
    segments = dedup

    # 2) 无缝碎片(<0.25s 间隔且极短无句末标点)并入前段；防长句堆叠失真
    merged: list[dict] = []
    for s in segments:
        if merged:
            prev = merged[-1]
            gap = s["start"] - prev["end"]
            if (
                gap < _MERGE_MAX_GAP
                and len(s["text"]) <= _MERGE_MAX_CHARS
                and len(prev["text"]) + len(s["text"]) <= _MERGE_MAX_TOTAL
                and not s["text"].endswith(_END_PUNCT)
                and not prev["text"].endswith(_END_PUNCT)
            ):
                prev["text"] = (prev["text"] + s["text"]).strip()
                prev["end"] = s["end"]
                stats["merged"] += 1
                continue
        merged.append(dict(s))
    segments = merged

    # 3) 段内连读幻觉压缩
    for s in segments:
        t = _compress_inner(s["text"])
        if t != s["text"]:
            s["text"] = t
            stats["compressed"] += 1

    # 4) 超限均匀采样（保开头结尾、中间按序隔取）
    total_chars = sum(len(s["text"]) for s in segments)
    stats["cleaned_chars"] = total_chars
    if total_chars > _TRANSCRIPT_MAX_CHARS:
        keep = [s for s in segments if s["text"]]
        if keep:
            # 先保首尾各 2 条，再对剩余按比例隔取到总字符接近上限
            head, tail = keep[:2], keep[-2:]
            mid = keep[2:-2] or []
            budget = _TRANSCRIPT_MAX_CHARS - sum(len(s["text"]) for s in head + tail)
            picked: list[dict] = []
            acc = 0
            step = max(1, sum(len(s["text"]) for s in mid) // max(1, budget))
            for s in mid:
                acc += len(s["text"])
                if acc >= step:
                    picked.append(s)
                    acc = 0
                if sum(len(x["text"]) for x in picked) >= budget:
                    break
            segments = head + picked + tail
            stats["dropped"] = max(0, stats["original"] - len(segments))
    return segments, stats


_PROOFREAD_SYSTEM = (
    "你是中文语音转写（ASR）校对专家。输入是短视频口播的逐句转写，存在同音/近音错别字。\n"
    "任务：结合上下文语义，把每行里的错别字改成正确的常用词，保证「原话可逐句保存」。\n"
    "参考改法：『在客厂写』→『在课堂写』；『宁感来了』→『灵感来了』；"
    "『写自己在等』→『写自己在这等』。\n"
    "硬性要求：\n"
    "1) 只改【待改】行，【语境】行只读，一律不得修改；\n"
    "2) 逐行独立校对，行数、行序、idx 一律不变，严禁合并、拆分、调换行；\n"
    "3) 只改错别字，不改写句式、不增删内容，改后每行字数与原文相差不超过 2 个字；\n"
    "4) 结合上下文判断同音词，拿不准的行宁可不改，不要凭空替换专有名词或人名；\n"
    "5) 只输出确实有改动的行；无改动的行不要出现在结果里；\n"
    "6) 同一个错词/同音词若在多行重复出现，必须逐行全部改掉，不要只改其中一处；\n"
    '7) 输出严格 JSON：{"items":[{"idx":<行号>,"text":"修正后整行文本"}]}\n'
    "8) 输出一律使用简体中文。"
)


def _iter_proofread_batches(texts: list[str]) -> list[tuple[int, int]]:
    """按行数与字符数切批，返回 [(start, end), ...]（左闭右开）。"""
    batches: list[tuple[int, int]] = []
    n = len(texts)
    start = 0
    while start < n and len(batches) < _PROOFREAD_MAX_BATCHES:
        chars = 0
        end = start
        while end < n and end - start < _PROOFREAD_BATCH_LINES:
            line_len = len(texts[end]) + 8  # +idx/标签开销
            if end > start and chars + line_len > _PROOFREAD_BATCH_CHARS:
                break
            chars += line_len
            end += 1
        batches.append((start, end))
        start = end
    return batches


def acceptable_fix(old: str, new: str) -> tuple[bool, str]:
    """校对结果护栏：拒绝合并/改写/超长变形，只接受「近似逐字替换」。

    弱模型（glm-4-flash 一档）容易顺手改写甚至合并相邻行，一旦写回会污染原话，
    因此落库前统一校验：单行、字数变化 ≤2（长句放宽到 20%）、相似度 ≥0.5。
    """
    if not new:
        return False, "空文本"
    if "\n" in new or "\r" in new:
        return False, "含换行（疑似合并多行）"
    delta = len(new) - len(old)
    limit = max(2, int(len(old) * 0.2))
    if abs(delta) > limit:
        return False, f"字数变化过大({delta:+d})"
    ratio = difflib.SequenceMatcher(None, old, new).ratio()
    if ratio < 0.5:
        return False, f"相似度过低({ratio:.2f})"
    return True, ""


def substitution_only_fix(old: str, new: str) -> tuple[bool, str]:
    """第二轮复查护栏：只接受「等长 + 仅替换 1~2 个同音字」。

    第一轮之后仍未改动的行，用更严格的等长替换规则复查一遍：
    增删字（如「等我」→「等着我」）、改词（如「这回」→「这次」）一律拒绝，
    确保补漏只补同音错字，绝不把模型顺手改写的内容写回原话。
    """
    if not new or len(new) != len(old):
        return False, "长度不一致（含增删字，拒绝改写）"
    diff = sum(1 for a, b in zip(old, new) if a != b)
    if diff == 0:
        return False, "无改动"
    limit = 1 if len(old) <= 8 else 2
    if diff > limit:
        return False, f"替换字过多({diff})"
    return True, ""


def _collect_fix_spans(olds: list[str], news: list[str]) -> list[tuple[str, str]]:
    """从已采纳的整行改动中抽出「等长替换」片段（2~4 字），用于同词同改传播。

    只取 difflib 判定为 replace 且两侧等长的多字片段（如 客厂→课堂、腐脂→构思）；
    增删/语序调整、以及单字替换一律不取——单字（如 宁→灵）在别处可能是人名或
    正常用字，全局传播会误伤，宁可留给第一轮按上下文判断。
    """
    spans: dict[str, str] = {}
    for old, new in zip(olds, news):
        if not old or not new:
            continue
        matcher = difflib.SequenceMatcher(None, old, new, autojunk=False)
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag != "replace":
                continue
            a, b = old[i1:i2], new[j1:j2]
            if not 2 <= len(a) <= 4 or len(a) != len(b):
                continue
            diff = sum(1 for x, y in zip(a, b) if x != y)
            if 0 < diff <= 2:
                spans[a] = b
    return list(spans.items())


async def _proofread_batch(
    batch_no: int, lines: list[str], system: str | None = None
) -> dict[int, str]:
    """单批校对（含 1 次重试）；返回 {idx: 修正文本}，失败返回 {}。"""
    from app.ai import chat  # 延迟导入避免模块级循环

    for attempt in range(2):
        try:
            res = await chat(
                messages=[
                    {"role": "system", "content": system or _PROOFREAD_SYSTEM},
                    {"role": "user", "content": "\n".join(lines)},
                ],
                model="flash",
                json_mode=True,
                scene="asr_proofread",
                timeout=120,
            )
            payload, _repaired = parse_json_loose(res.get("reply") or "")
            fixes: dict[int, str] = {}
            for item in (payload or {}).get("items", []):
                if not isinstance(item, dict) or "idx" not in item:
                    continue
                text = str(item.get("text") or "").strip()
                if not text:
                    continue
                try:
                    fixes[int(item["idx"])] = text
                except (TypeError, ValueError):
                    continue
            return fixes
        except Exception as exc:  # noqa: BLE001 单批失败不影响其它批
            if attempt == 0:
                logger.info("ASR 校对第 %d 批解析失败，重试：%s", batch_no, exc)
                continue
            logger.warning("ASR 校对第 %d 批失败，跳过该批：%s", batch_no, exc)
    return {}


async def proofread_segments(segments: list[dict]) -> list[dict]:
    """分批 + 上下文纠错：修正 whisper 中文转写的同音错别字。

    与旧实现的差异（对应生产暴露的两个问题）：
    - 旧版超 6000 字符直接跳过校对 → 长视频一个字都不校；现改为分批，永不整体跳过；
    - 旧版一次性把全文丢给模型、无上下文约束 → 改动零散且会改写/合并；现按批喂
      「上文/待改/下文」，并逐条过 ``acceptable_fix`` 护栏后才写回；
    - 弱模型会漏改重复出现的同音词 → 第一轮采纳的等长替换映射再「同词传播」一遍，
      把同一错词在全文的其余出现位置确定性补齐。

    任一步失败都回退原文，绝不影响 ASR 主结果；被采纳的行额外落 ``raw_text``
    保留原话，便于回溯「改了什么」。
    """
    if not segments:
        return segments
    texts = [str(s.get("text") or "") for s in segments]
    batches = _iter_proofread_batches(texts)
    if not batches:
        return segments
    if len(batches) >= _PROOFREAD_MAX_BATCHES and batches[-1][1] < len(texts):
        logger.warning(
            "ASR 校对批次数触顶（%d 批），第 %d 段之后未校对",
            _PROOFREAD_MAX_BATCHES, batches[-1][1],
        )

    # 各批并行（信号量限流），批内行序不变
    sem = asyncio.Semaphore(_PROOFREAD_CONCURRENCY)

    def _render(indices: list[int]) -> list[str]:
        targets = set(indices)
        lo = max(0, min(indices) - _PROOFREAD_CONTEXT_LINES)
        hi = min(len(texts), max(indices) + _PROOFREAD_CONTEXT_LINES + 1)
        return [f"[{'待改' if i in targets else '语境'}] {i}\t{texts[i]}" for i in range(lo, hi)]

    async def _run(batch_no: int, indices: list[int], system: str | None = None) -> dict[int, str]:
        async with sem:
            return await _proofread_batch(batch_no, _render(indices), system)

    results = await asyncio.gather(
        *(_run(no, list(range(s, e))) for no, (s, e) in enumerate(batches, start=1)),
        return_exceptions=True,
    )

    out: list[dict] = [dict(s) for s in segments]
    applied = rejected = propagated = 0
    fixed_idx: list[int] = []  # 第一轮已采纳改动的行号（传播时跳过）
    for (start, end), got in zip(batches, results):
        for idx, new_text in (got or {}).items() if not isinstance(got, Exception) else ():
            if not (start <= idx < end):  # 模型越界改「语境」行 → 丢弃
                rejected += 1
                continue
            old = texts[idx].strip()
            fixed = to_simplified(new_text).strip()
            if not old or not fixed or fixed == old:
                continue
            ok, why = acceptable_fix(old, fixed)
            if not ok:
                rejected += 1
                logger.info("ASR 校对丢弃 idx=%d（%s）：%r → %r", idx, why, old, fixed)
                continue
            out[idx]["raw_text"] = old
            out[idx]["text"] = fixed
            out[idx]["proofread"] = True
            fixed_idx.append(idx)
            applied += 1

    # 同词同改传播：第一轮已采纳的「错词→正确词」映射，在同一错误反复出现、
    # 但弱模型只改了其中几处时，把映射补齐到全文其它同词位置（等长替换，确定性落库）。
    if _PROOFREAD_PROPAGATE:
        for old_span, new_span in _collect_fix_spans(
            [texts[i] for i in fixed_idx], [out[i]["text"] for i in fixed_idx]
        ):
            for idx, line in enumerate(texts):
                if idx in fixed_idx or not line or old_span not in line:
                    continue
                fixed = to_simplified(line.replace(old_span, new_span)).strip()
                if fixed == line:
                    continue
                ok, why = substitution_only_fix(line, fixed)
                if not ok:
                    rejected += 1
                    logger.info("ASR 同词传播丢弃 idx=%d（%s）：%r → %r", idx, why, line, fixed)
                    continue
                out[idx]["raw_text"] = line
                out[idx]["text"] = fixed
                out[idx]["proofread"] = True
                propagated += 1

    logger.info(
        "ASR 校对：批 %d 段 %d，采纳 %d（含同词传播 %d），丢弃 %d",
        len(batches), len(segments), applied + propagated, propagated, rejected,
    )
    return out
