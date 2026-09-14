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
