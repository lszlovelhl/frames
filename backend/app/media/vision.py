"""视觉模型：批量抽帧画面理解 → 分段画面简报。

分批（每批 ≤4 帧）送智谱 glm-4v-flash，返回精简 JSON：
{frames: [{seq, desc, text_overlay, emotion, style}]}
desc 只描述"画面里有什么/发生了什么"，不推断好坏。
"""
import asyncio
import base64
import json
import logging
from pathlib import Path

from app.ai import chat

logger = logging.getLogger(__name__)

BATCH = 4

_SYSTEM = """你是爆款视频拆解团队的\"画面观察员\"。你只描述视频画面中客观可见的信息，不做好坏评价、不做营销推断。
对给定的每一帧画面，用中文输出：
- desc: 画面内容描述（60字内，说清主体、场景、动作、构图、字幕/贴纸文字）
- text_overlay: 画面中出现的文字（标题字、字幕、贴纸、弹幕式文案等，无则空串）
- emotion: 画面传达的情绪氛围（一词，如 紧张/搞笑/温馨/悬念/亢奋）
- style: 视觉风格（一词，如 实拍/动画/绿幕抠像/录屏/混剪）
逐帧编号输出，字段固定，必须全部返回。"""


def _encode(path: str) -> str:
    return base64.b64encode(Path(path).read_bytes()).decode()


def _normalize(raw: str) -> list[dict]:
    """兼容模型返回形态：可能是 dict{frames:[...]} / 数组 / 单帧对象 / 代码块包裹。"""
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    data = json.loads(text)

    def _frames_of(item):
        if isinstance(item, dict):
            inner = item.get("frames")
            if isinstance(inner, list):
                return [x for x in inner if isinstance(x, dict)]
            if {"seq", "desc"} & set(item):
                return [item]
        return []

    if isinstance(data, list):
        out: list[dict] = []
        for it in data:
            out.extend(_frames_of(it))
        return out
    if isinstance(data, dict):
        return _frames_of(data)
    return []


async def _describe_batch(frames: list[dict]) -> dict:
    content: list[dict] = [
        {"type": "text", "text": "请按帧序号逐帧描述以下画面："}
    ]
    for f in frames:
        content.append(
            {
                "type": "text",
                "text": f"[帧 seq={f['seq']} 时间段 {f['start_ms']}-{f['end_ms']}ms]",
            }
        )
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{_encode(f['path'])}"},
            }
        )
    content.append(
        {
            "type": "text",
            "text": (
                '必须输出 JSON：{"frames":[{"seq":0,"desc":"...","text_overlay":"...",'
                '"emotion":"...","style":"..."}]}'
            ),
        }
    )
    messages = [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": content},
    ]
    for attempt in range(2):
        try:
            result = await chat(
                messages=messages,
                model="vision",
                temperature=0.1 if attempt == 0 else 0.4,
                max_tokens=1024,  # glm-4v-flash 上限 1024，超限返回 400(code 1210)
                json_mode=True,
                timeout=180,
                scene="vision",
            )
            raw = result.get("reply") or ""
            if not raw.strip():
                continue
            items = _normalize(raw)
            if items:
                return {"frames": items}
        except Exception as exc:  # noqa: BLE001
            logger.warning("vision batch 失败 attempt%s: %s", attempt + 1, exc)
    return {}


async def describe_frames(frames: list[dict]) -> list[dict]:
    """输入抽帧列表，输出每帧简报 {seq,desc,text_overlay,emotion,style}"""
    briefs: dict[int, dict] = {}
    loop = asyncio.get_running_loop()
    sem = asyncio.Semaphore(2)  # 控制并发，避免触发限流

    async def _one(batch: list[dict]):
        async with sem:
            return await _describe_batch(batch)

    batches = [frames[i : i + BATCH] for i in range(0, len(frames), BATCH)]
    results = await asyncio.gather(*(_one(b) for b in batches))
    for b, data in zip(batches, results):
        for item in data.get("frames", []) or []:
            seq = item.get("seq")
            try:
                key = int(seq)
            except (TypeError, ValueError):
                continue
            if key in briefs:
                continue
            briefs[key] = {
                "seq": key,
                "desc": str(item.get("desc") or ""),
                "text_overlay": str(item.get("text_overlay") or ""),
                "emotion": str(item.get("emotion") or ""),
                "style": str(item.get("style") or ""),
            }
    # 对齐原帧顺序
    ordered = []
    for f in frames:
        b = briefs.get(f["seq"])
        if b:
            ordered.append({**f, **b})
        else:
            ordered.append({**f, "desc": "", "text_overlay": "", "emotion": "", "style": ""})
    return ordered


_SCENE_SYSTEM = """你是短视频镜头叙事分析师。你的任务是"看完"一部视频的全部逐帧简报，把零散的帧合并成
场景（scene）时间轴——同一镜头/同一叙事单元的连续画面归为一个场景。这就是你对整部视频的画面记忆。

输入：逐帧简报（seq/时间段/画面描述/风格/画面文字）+ 画面动态事件（转场点/运动爆发段）。
输出 JSON：{"scenes":[{"start_ms":..., "end_ms":..., "subject":"...", "action":"...",
"style":"...", "text_overlay":"...", "change_note":"..."}]}

规则（必须严格遵守）：
- 场景 = 叙事单元，不是帧。同一主体/同一动作/同一场景的连续帧必须合并成一个场景。
  只有当画面主体、场景地点、或叙事推进发生明显变化时才开新场景。
- 场景数 4~15 个。短片中 8~12 个为佳；把 30+ 帧压到 8~12 个场景，不要逐帧切。
- 场景边界优先采用动态事件的转场点（画面剧变处），其次按画面主体/场景切换。
- subject 一句话说清主体/场景（≤20 字）；action 写这段画面里持续发生的动作或变化（≤20 字）；
  style 用一词（实拍/动画/混剪/绿幕…）；text_overlay 写画面出现的文字（无则空串）；
  change_note 写【叙事层面的切换原因】（≤30 字，例如"钩子结束进入铺垫"、"爆炸高潮"、
  "转折：肉块被发现"），禁止写"场景切换"这种空话，必须说清叙事推进到哪一步。
- 覆盖完整时间轴（首场景从 0 开始，末场景到视频结束），场景不重叠、按时间排序。
- 只输出 JSON，不要解释。"""


def _coalesce_scenes(scenes: list[dict], max_scenes: int = 15) -> list[dict]:
    """harness 兜底合并：模型给的场景过碎时，按叙事单元确定性合并。

    合并条件（相邻场景满足任一）：
    - action 相同（同一持续动作的连续帧）
    - change_note 相同（同一叙事推进）
    - subject 相同且 style 相同（同一主体同一风格的连续画面）
    """
    if len(scenes) <= max_scenes:
        return scenes
    out: list[dict] = []

    def _same(a: dict, b: dict) -> bool:
        if (a.get("action") or "") and a.get("action") == b.get("action"):
            return True
        if (a.get("change_note") or "") and a.get("change_note") == b.get("change_note"):
            return True
        return (a.get("subject") == b.get("subject")) and (a.get("style") == b.get("style"))

    for s in scenes:
        if out and _same(out[-1], s):
            prev = out[-1]
            prev["end_ms"] = max(int(prev["end_ms"]), int(s["end_ms"]))
            if len(str(s.get("subject") or "")) > len(str(prev.get("subject") or "")):
                prev["subject"] = s["subject"]
            if len(str(s.get("action") or "")) > len(str(prev.get("action") or "")):
                prev["action"] = s["action"]
            if s.get("text_overlay") and not prev.get("text_overlay"):
                prev["text_overlay"] = s["text_overlay"]
            prev["change_note"] = s.get("change_note") or prev.get("change_note")
        else:
            out.append(dict(s))

    # 仍超限：等比强制合并（保首尾）
    if len(out) > max_scenes:
        forced: list[dict] = []
        n = len(out)
        step = n / max_scenes
        i = 0
        while i < n:
            j = min(n - 1, int(round((i + 1) * step - 1)))
            j = max(j, i)
            chunk = out[i : j + 1]
            merged = dict(chunk[0])
            merged["end_ms"] = int(chunk[-1]["end_ms"])
            merged["action"] = "、".join(
                dict.fromkeys(str(c.get("action") or "") for c in chunk if c.get("action"))
            )[:60]
            forced.append(merged)
            i = j + 1
        out = forced
    for seq, s in enumerate(out, start=1):
        s["seq"] = seq
    return out


async def merge_scenes(
    frames: list[dict], dynamic_events: list[dict] | None = None
) -> list[dict]:
    """把逐帧简报合并成场景时间轴（模型分层记忆的场景层）。

    输入 frames 须已含 desc/style/text_overlay（describe_frames 之后）。
    """
    if not frames:
        return []
    briefs = [
        {
            "seq": f.get("seq"), "start_ms": f.get("start_ms"), "end_ms": f.get("end_ms"),
            "desc": (f.get("desc") or "")[:80],
            "style": f.get("style") or "",
            "text_overlay": f.get("text_overlay") or "",
        }
        for f in frames
    ]
    # 帧数过多时 GLM flash 输入超限稳定失败（43 帧 fail / 33 帧 OK）：
    # harness 自适应降采样，均匀抽到 ≤33 帧，保持完整时间覆盖
    if len(briefs) > 33:
        step = len(briefs) / 33
        keep = sorted({min(len(briefs) - 1, int(i * step)) for i in range(33)})
        briefs = [briefs[i] for i in keep]
    evs = [
        {
            "event_type": e.get("event_type"), "t_ms": e.get("t_ms"),
            "intensity": e.get("intensity"), "note": (e.get("note") or "")[:40],
        }
        for e in (dynamic_events or [])
    ]
    user = (
        "【逐帧简报】\n"
        + json.dumps(briefs, ensure_ascii=False)
        + "\n\n【画面动态事件】\n"
        + json.dumps(evs, ensure_ascii=False)
    )
    for attempt in range(2):
        try:
            result = await chat(
                messages=[
                    {"role": "system", "content": _SCENE_SYSTEM},
                    {"role": "user", "content": user},
                ],
                model="flash",
                temperature=0.2,
                max_tokens=4096,
                json_mode=True,
                timeout=180,
                scene="scene_memory",
            )
            raw = result.get("reply") or ""
            if not raw.strip():
                continue
            data = json.loads(raw)
            scenes = data.get("scenes") or []
            if not scenes:
                continue
            out: list[dict] = []
            for s in scenes:
                try:
                    start = int(s.get("start_ms") or 0)
                    end = int(s.get("end_ms") or start)
                except (TypeError, ValueError):
                    continue
                if end <= start:
                    continue
                out.append(
                    {
                        "start_ms": start,
                        "end_ms": end,
                        "subject": str(s.get("subject") or "")[:80],
                        "action": str(s.get("action") or "")[:80],
                        "style": str(s.get("style") or "")[:32],
                        "text_overlay": str(s.get("text_overlay") or "")[:120],
                        "change_note": str(s.get("change_note") or "")[:120],
                    }
                )
            if out:
                return _coalesce_scenes(out)
        except Exception as exc:  # noqa: BLE001
            logger.warning("merge_scenes 失败 attempt%s: %s", attempt + 1, exc)
    return []
