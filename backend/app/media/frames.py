"""自适应抽帧：为分段画面理解准备关键帧。

采样策略（按内容分档）：
- 运动/画面快速变化的区间  → 密集抽帧（长视频 ~1.2s；短视频 ~0.8s）
- 普通平稳区间            → 常规抽帧（长视频 ~4s；短视频 ~1.6s）
- 大量重复/静止画面区间    → 低密度抽帧（长视频 ~12s；短视频 ~4s）
- 场景切换尖峰 + ASR 每段有语音锚点 → 强制补帧，保证重要时刻不漏
- **短视频（<5 分钟）切换独立密集档**：帧数上限 120、目标平均 ~1.5s 一帧，
  避免短视频关键动作/字幕被稀疏采样漏掉（见 profile_for）

做法：先用 ffmpeg 以 1fps / 160x90 灰度探测整片，算相邻帧差异序列，
据此对每秒划分档位并推进采样网格；预算超限时等比放宽并保锚点。
输出 [{seq,start_ms,end_ms,time_ms,path,kind}]，jpg 宽 <=1024。
"""
import asyncio
import logging
import subprocess
from pathlib import Path
from typing import Any

from app.media import ffbin

logger = logging.getLogger(__name__)

PROBE_W, PROBE_H = 160, 90
MAX_FRAMES = 35          # 长视频视觉成本上限（去重后30-35帧足够）
MIN_FRAMES = 10
# 分档阈值（diffs 分位）
_HI = 82
_LO = 45
# 各档间隔（秒）——长视频
IV_HIGH = 1.2
IV_MID = 4.0
IV_STATIC = 12.0
# 场景切换尖峰分位
_PEAK = 95

# ---------------------------------------------------------------
# 短视频（<5 分钟）更密集抽帧档
# 短视频信息密度高、单帧信息量大，稀疏采样容易漏掉关键动作/字幕，
# 因此收紧各档间隔、抬高帧数上限（目标：平均 ~1.5s 一帧）。
# ---------------------------------------------------------------
# 抽帧策略版本：分档间隔 / 帧数上限 / 锚点规则任何变更都要递增。
# services/media_prep.ensure_media 复用素材前会比对该版本，落后则强制重抽帧
# （否则「复用已有素材」短路会让密集档对老链接永远不生效）。
FRAMES_POLICY_VERSION = 7  # v7：按实际帧率抽帧（每秒 3 帧）+ 组合缩略图（3×3）

SHORT_MAX_S = 300.0          # 时长阈值：< 5 分钟按短视频处理
SHORT_IV_HIGH = 0.8
SHORT_IV_MID = 1.6
SHORT_IV_STATIC = 4.0
SHORT_MAX_FRAMES = 40       # 短视频帧数上限（去重后30-40帧足够）
SHORT_MIN_FRAMES = 15
SHORT_FRAME_INTERVAL = 2.0   # 短视频目标平均间隔（秒）：去重后保证信息密度


def profile_for(duration: float) -> dict:
    """按时长选择抽帧档位：短视频更密集，长视频保持原策略。"""
    if duration < SHORT_MAX_S:
        return {
            "mode": "short",
            "iv": (SHORT_IV_HIGH, SHORT_IV_MID, SHORT_IV_STATIC),
            "budget": max(
                SHORT_MIN_FRAMES,
                min(SHORT_MAX_FRAMES, int(round(duration / SHORT_FRAME_INTERVAL))),
            ),
            "min_frames": SHORT_MIN_FRAMES,
        }
    return {
        "mode": "long",
        "iv": (IV_HIGH, IV_MID, IV_STATIC),
        "budget": max(MIN_FRAMES, min(MAX_FRAMES, int(duration / 3.0))),
        "min_frames": MIN_FRAMES,
    }


def _ffprobe_duration(video_path: str) -> float:
    out = subprocess.run(
        [ffbin.ffprobe_bin(), "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", video_path],
        check=True, capture_output=True, text=True,
    )
    return float(out.stdout.strip())


def _ffprobe_fps(video_path: str) -> float:
    """获取视频实际帧率（fps）。"""
    out = subprocess.run(
        [ffbin.ffprobe_bin(), "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=r_frame_rate",
         "-of", "csv=p=0", video_path],
        check=True, capture_output=True, text=True,
    )
    raw = out.stdout.strip()
    # r_frame_rate 通常是 "30/1" 或 "29.97/1" 格式
    if "/" in raw:
        num, den = raw.split("/", 1)
        try:
            return float(num) / float(den)
        except (ValueError, ZeroDivisionError):
            pass
    try:
        return float(raw)
    except ValueError:
        return 30.0  # 默认 30fps


def _probe_diffs(video_path: str, duration: float) -> list[float]:
    """1fps 灰度探测 → 相邻帧平均绝对差序列（长度≈帧数-1）。"""
    n_probe = max(2, min(900, int(duration) + 2))
    cmd = [
        ffbin.ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-i", video_path,
        "-vf", f"fps=1,scale={PROBE_W}:{PROBE_H}:force_original_aspect_ratio=decrease,"
               f"pad={PROBE_W}:{PROBE_H}:(ow-iw)/2:(oh-ih)/2,format=gray",
        "-frames:v", str(n_probe), "-f", "rawvideo", "pipe:1",
    ]
    proc = subprocess.run(cmd, capture_output=True, timeout=180)
    raw = proc.stdout
    n = len(raw) // (PROBE_W * PROBE_H)
    if n < 2:
        return []
    import numpy as np
    arr = np.frombuffer(raw[: n * PROBE_W * PROBE_H], dtype=np.uint8).reshape(n, PROBE_H, PROBE_W)
    arr = arr.astype(np.int16)
    diffs = [float(np.abs(arr[i + 1] - arr[i]).mean()) for i in range(n - 1)]
    return diffs


def _interval_for(diff: float, hi: float, lo: float) -> float:
    if diff >= hi:
        return IV_HIGH
    if diff >= lo:
        return IV_MID
    return IV_STATIC


def _anchor_times(segments: list[dict] | None) -> list[float]:
    """ASR 段落锚点：仅对含文本的段，取其起点+0.25s（关键开口瞬间）。"""
    if not segments:
        return []
    out = []
    for s in segments:
        text = (s.get("text") or "").strip()
        if not text:
            continue
        start = float(s.get("start") or 0)
        end = float(s.get("end") or 0)
        if end - start < 0.4:
            continue
        out.append(start + 0.25)
    return out


def _plan_times(
    duration: float,
    diffs: list[float],
    segments: list[dict] | None,
    budget: int,
    iv: tuple[float, float, float] = (IV_HIGH, IV_MID, IV_STATIC),
) -> list[float]:
    """返回采样时刻列表（秒），按内容分档 + 锚点。iv 为 (高档, 中档, 静帧档) 间隔。"""
    iv_high, iv_mid, iv_static = iv
    if not diffs:
        # 探测失败：均匀常规采样
        step = max(1.0, duration / max(budget, 1))
        return [i * step for i in range(int(duration // step) + 1)][:budget]

    import numpy as np
    d = np.array(diffs, dtype=np.float64)
    hi = float(np.percentile(d, _HI))
    lo = float(np.percentile(d, _LO))
    peak_thr = float(np.percentile(d, _PEAK))
    last_peak = float(np.percentile(d, 98))

    times: list[float] = []
    t = 0.0
    while t < duration:
        idx = min(int(t), len(d) - 1)
        diff = float(d[idx])
        if diff >= hi:
            step = iv_high
        elif diff >= lo:
            step = iv_mid
        else:
            step = iv_static
        # 事件补帧：探测到强尖峰且与上一帧间隔 >0.6s
        if diff >= peak_thr and (not times or t - times[-1] > 0.6):
            if times and t - times[-1] < 0.5:
                pass  # 太近不重复加
            else:
                times.append(round(t, 3))
            if diff >= last_peak:
                # 极强切换点：下一帧仍给它附近
                times.append(round(min(t + 0.6, duration), 3))
        else:
            times.append(round(t, 3))
        t += step

    # 合并 ASR 锚点（间隔过近则跳过；短视频允许更小间隔）
    anchor_gap = max(0.6, iv_high * 0.9)
    for a in _anchor_times(segments):
        a = round(min(a, duration - 0.2), 3)
        if a < 0:
            continue
        if not times or all(abs(a - x) > anchor_gap for x in times):
            times.append(a)
    times = sorted(set(round(min(max(x, 0.0), max(duration - 0.1, 0.0)), 3) for x in times))

    # 去重过于贴近的帧（默认 <0.45s 保留前者；短视频按档位收紧到 0.3s）
    min_gap = 0.3 if iv_high < 1.0 else 0.45
    dedup: list[float] = []
    for x in times:
        if not dedup or x - dedup[-1] >= min_gap:
            dedup.append(x)
    times = dedup

    # 预算超限：等比放宽（保锚点=强尖峰与 ASR 锚点优先）
    if len(times) > budget:
        anchors = set(
            round(x, 2) for x in times
            if (any(abs(x - a) < 0.2 for a in _anchor_times(segments)))
            or (diffs and diffs[min(int(x), len(diffs) - 1)] >= peak_thr)
        )
        keep = [x for x in times if round(x, 2) in anchors]
        rest = [x for x in times if round(x, 2) not in anchors]
        n_rest = budget - len(keep)
        if n_rest > 0 and rest:
            step_i = max(1, len(rest) // n_rest)
            keep.extend(rest[::step_i][:n_rest])
        times = sorted(keep)[:budget]
    return times


def _dedup_frames_by_similarity(frames: list[dict[str, Any]], similarity_threshold: float = 0.92) -> list[dict[str, Any]]:
    """滑动窗口去重：去掉和前N帧相似度太高的重复镜头。
    
    借鉴 claude-real-video 的思路：A-B-A 切换的镜头只保留一次，
    避免重复占用模型上下文。
    """
    if len(frames) <= 5:
        return frames
    
    try:
        from PIL import Image
        import numpy as np
    except ImportError:
        logger.warning("Pillow未安装，跳过去重")
        return frames
    
    kept: list[dict[str, Any]] = []
    window_size = 4  # 滑动窗口：和前4帧比较
    
    for frame in frames:
        path = frame.get("path")
        if not path:
            kept.append(frame)
            continue
        
        try:
            # 缩小到32x32计算相似度，速度快
            img = Image.open(path).convert("L").resize((32, 32))
            arr = np.array(img, dtype=np.float32) / 255.0
        except Exception:
            kept.append(frame)
            continue
        
        # 和窗口内的帧比较
        is_duplicate = False
        recent = kept[-window_size:]
        for prev in recent:
            prev_path = prev.get("path")
            if not prev_path:
                continue
            try:
                prev_img = Image.open(prev_path).convert("L").resize((32, 32))
                prev_arr = np.array(prev_img, dtype=np.float32) / 255.0
                # 计算余弦相似度
                sim = float(np.dot(arr.flatten(), prev_arr.flatten()) / 
                           (np.linalg.norm(arr) * np.linalg.norm(prev_arr) + 1e-8))
                if sim > similarity_threshold:
                    is_duplicate = True
                    break
            except Exception:
                continue
        
        if not is_duplicate:
            kept.append(frame)
    
    logger.info(f"帧去重：{len(frames)} → {len(kept)}（去掉{len(frames)-len(kept)}重复帧）")
    return kept


def detect_dynamic_events(
    diffs: list[float], duration: float, interval_s: float = 1.0
) -> list[dict]:
    """从 1fps 帧差序列推导画面动态事件轨（harness 精确层，不依赖模型）。

    返回 [{seq, event_type, t_ms, duration_ms, intensity(0~1), note}]：
    - transition：孤立尖峰（画面剧变 = 剪辑/硬切点）
    - motion_burst：连续高运动段（高速动作/卡点动画）
    """
    if not diffs or len(diffs) < 3:
        return []
    import numpy as np

    d = np.array(diffs, dtype=np.float64)
    dmax = float(d.max())
    if dmax <= 0:
        return []
    norm = d / dmax
    p85 = float(np.percentile(norm, 85))
    p92 = float(np.percentile(norm, 92))
    events: list[dict] = []

    # 1) 转场点：孤立尖峰（≥p92 且显著高于左右邻）
    for i in range(1, len(norm) - 1):
        v = float(norm[i])
        if v >= p92 and v >= 1.3 * float(norm[i - 1]) and v >= 1.3 * float(norm[i + 1]):
            events.append({
                "event_type": "transition",
                "t_ms": int(i * interval_s * 1000),
                "duration_ms": int(interval_s * 1000),
                "intensity": round(v, 3),
                "note": f"画面剧变（帧差峰值 {round(v * dmax)}）",
            })

    # 2) 运动爆发段：连续 ≥2 点 ≥p85
    i = 0
    while i < len(norm):
        if float(norm[i]) >= p85:
            j = i
            while j + 1 < len(norm) and float(norm[j + 1]) >= p85:
                j += 1
            if j - i >= 1:  # 连续 ≥2 秒
                burst = float(norm[i : j + 1].mean())
                events.append({
                    "event_type": "motion_burst",
                    "t_ms": int(i * interval_s * 1000),
                    "duration_ms": int((j - i + 1) * interval_s * 1000),
                    "intensity": round(burst, 3),
                    "note": f"连续高运动 {j - i + 1}s（均值 {round(burst * dmax)}）",
                })
            i = j + 1
        else:
            i += 1

    # 时间轴排序 + 去重（同秒的 transition 与 burst 并存时保留 transition）
    events.sort(key=lambda e: (e["t_ms"], 0 if e["event_type"] == "transition" else 1))
    dedup: list[dict] = []
    for e in events:
        if dedup and abs(e["t_ms"] - dedup[-1]["t_ms"]) < 500:
            continue
        dedup.append(e)
    for seq, e in enumerate(dedup, start=1):
        e["seq"] = seq
    return dedup


async def sample_frames(
    video_path: str,
    work_dir: Path,
    segments: list[dict] | None = None,
    duration_hint: float | None = None,
    plan_info: dict | None = None,
) -> list[dict[str, Any]]:
    """返回 [{seq,start_ms,end_ms,time_ms,path,kind}]。kind: motion/steady/static/anchor

    时长 <5 分钟走短视频密集档（间隔收紧、帧数上限抬高）；plan_info 会被填入本次
    采用的档位信息（mode/budget/iv），供 manifest 落库与自检。
    """
    frames_dir = work_dir / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)

    duration = duration_hint or _ffprobe_duration(video_path)
    if duration <= 0:
        return []

    # v7：按实际帧率抽帧（每秒 3 帧），替代原来的按内容分档抽帧
    # 24fps → 每 8 帧抽 1 帧 = 每秒 3 帧
    # 30fps → 每 10 帧抽 1 帧 = 每秒 3 帧
    # 60fps → 每 20 帧抽 1 帧 = 每秒 3 帧
    fps = _ffprobe_fps(video_path)
    target_fps = 3.0  # 每秒 3 帧
    frame_interval = 1.0 / target_fps  # 每 0.333 秒抽 1 帧

    # 计算总帧数
    total_frames = int(duration * target_fps)
    budget = total_frames

    if plan_info is not None:
        plan_info.update(
            {"mode": "fps_based", "duration_s": round(duration, 2),
             "fps": fps, "target_fps": target_fps, "budget": budget,
             "policy_version": FRAMES_POLICY_VERSION}
        )

    # 按帧率均匀抽帧
    times = [i * frame_interval for i in range(total_frames) if i * frame_interval < duration]

    loop = asyncio.get_running_loop()
    try:
        diffs = await loop.run_in_executor(None, lambda: _probe_diffs(video_path, duration))
    except Exception:  # noqa: BLE001
        logger.warning("抽帧探测失败，回退均匀采样", exc_info=True)
        diffs = []

    prof = profile_for(duration)
    times = _plan_times(duration, diffs, segments, budget, iv=prof["iv"])
    if not times:
        step = max(1.0, duration / max(prof["min_frames"], 1))
        times = [i * step for i in range(prof["min_frames"]) if i * step < duration]

    import numpy as np
    hi = lo = None
    if diffs:
        d = np.array(diffs, dtype=np.float64)
        hi = float(np.percentile(d, _HI))
        lo = float(np.percentile(d, _LO))

    def _kind(t: float) -> str:
        if not diffs:
            return "steady"
        idx = min(int(t), len(diffs) - 1)
        diff = float(diffs[idx])
        if diff >= hi:
            return "motion"
        if diff >= lo:
            return "steady"
        return "static"

    async def _grab(seq: int, t: float) -> dict[str, Any] | None:
        path = frames_dir / f"f{seq:03d}.jpg"
        try:
            await loop.run_in_executor(
                None,
                lambda: subprocess.run(
                    [ffbin.ffmpeg_bin(), "-y", "-ss", f"{t:.2f}", "-i", video_path,
                     "-frames:v", "1", "-vf", "scale='min(1024,iw)':-2", "-q:v", "3",
                     str(path)],
                    check=True, capture_output=True,
                ),
            )
        except subprocess.CalledProcessError:
            logger.warning("抽帧失败 t=%s", t)
            return None
        return path

    frames: list[dict[str, Any]] = []
    prev_t: float | None = None
    for seq, t in enumerate(times):
        p = await _grab(seq, t)
        if p is None:
            continue
        end_t = (times[seq + 1] if seq + 1 < len(times) else min(t + 4.0, duration))
        start_ms = int(t * 1000)
        kind = _kind(t)
        is_anchor = bool(segments and any(
            (s.get("text") or "").strip()
            and abs(float(s.get("start") or 0) + 0.25 - t) < 0.3
            for s in segments
        ))
        frames.append({
            "seq": len(frames),
            "start_ms": start_ms,
            "end_ms": int(end_t * 1000),
            "time_ms": start_ms,
            "path": str(p),
            "kind": "anchor" if is_anchor else kind,
        })
        prev_t = t

    # 画面动态事件轨：1fps 帧差序列 → 转场点/运动爆发段（harness 精确层）
    events = detect_dynamic_events(diffs, duration)
    if plan_info is not None:
        plan_info["dynamic_events"] = events
    
    # 滑动窗口去重：去掉相似的重复镜头（A-B-A切换只保留一次）
    # 借鉴 claude-real-video 思路，减少token消耗
    frames = _dedup_frames_by_similarity(frames, similarity_threshold=0.90)
    
    # 重新编号
    for i, f in enumerate(frames):
        f["seq"] = i

    # v7：组合缩略图（3×3，9 张一图）
    # 把抽好的帧拼成 3×3 的组合图，减少模型需要看的图片数量
    frames = _make_contact_sheets(frames, frames_dir, cols=3, rows=3)
    
    return frames


def _make_contact_sheets(
    frames: list[dict[str, Any]],
    frames_dir: Path,
    cols: int = 3,
    rows: int = 3,
) -> list[dict[str, Any]]:
    """把帧拼成 3×3 组合缩略图（contact sheet）。

    每 9 张图拼成一张，减少模型需要看的图片数量。
    组合图的 path 指向 contact_sheet_XX.jpg。
    """
    if not frames:
        return frames

    from PIL import Image

    sheets_dir = frames_dir / "sheets"
    sheets_dir.mkdir(exist_ok=True)

    batch_size = cols * rows
    sheets = []

    for batch_idx in range(0, len(frames), batch_size):
        batch = frames[batch_idx:batch_idx + batch_size]
        if len(batch) < 2:
            # 只有 1 张图，不拼了
            sheets.extend(batch)
            continue

        # 读取所有图，统一尺寸
        images = []
        for f in batch:
            try:
                img = Image.open(f["path"])
                images.append(img)
            except Exception:
                continue

        if not images:
            continue

        # 统一尺寸：取第一张图的尺寸
        w, h = images[0].size
        # 每张图缩放到原来的 1/3
        thumb_w, thumb_h = w // cols, h // rows

        # 创建组合图
        sheet = Image.new("RGB", (thumb_w * cols, thumb_h * rows))

        for i, img in enumerate(images):
            row = i // cols
            col = i % cols
            img_thumb = img.resize((thumb_w, thumb_h), Image.Resampling.LANCZOS)
            sheet.paste(img_thumb, (col * thumb_w, row * thumb_h))

        # 保存组合图
        sheet_path = sheets_dir / f"sheet_{batch_idx // batch_size:03d}.jpg"
        sheet.save(sheet_path, quality=85)

        # 组合图的元数据：取第一张帧的时间，end_ms 取最后一张帧的 end_ms
        first = batch[0]
        last = batch[-1]
        sheets.append({
            "seq": batch_idx // batch_size,
            "start_ms": first["start_ms"],
            "end_ms": last["end_ms"],
            "time_ms": first["time_ms"],
            "path": str(sheet_path),
            "kind": "sheet",
            "frame_count": len(batch),
            "original_frames": [f["seq"] for f in batch],
        })

    return sheets
