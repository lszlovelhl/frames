"""ffmpeg / ffprobe 可执行文件定位（跨平台，解决 GUI/launchd 启动时 PATH 不全）。

问题背景
--------
macOS 上用 Homebrew 安装的 ffmpeg 位于 /opt/homebrew/bin（Intel 为 /usr/local/bin），
但由访达双击、launchd、IDE 或嵌入式方式启动的 Python 进程，其 PATH 往往只有
/usr/bin:/bin:/usr/sbin:/sbin，导致 ``subprocess.run(["ffprobe", ...])`` 抛
FileNotFoundError（Errno 2: 'ffprobe'），整条拆解链路直接 failed、无任何层产物。

解决方案
--------
模块导入时按「显式环境变量 → PATH → 常见安装目录」顺序解析真实绝对路径：

1. ``FRAMES_FFMPEG_BIN`` / ``FRAMES_FFPROBE_BIN``：直接指定可执行文件绝对路径；
2. ``FRAMES_FFMPEG_DIR``：指定 bin 目录，拼出 ``<dir>/ffmpeg`` 与 ``<dir>/ffprobe``；
3. ``shutil.which``：沿用当前 PATH；
4. 常见安装目录（Homebrew / MacPorts / /usr/local / snap / winget 等）逐个探测；
5. 都没找到时回退为裸命令名 ``ffmpeg`` / ``ffprobe``（保持原行为，由调用方报错）。

同时把命中的目录前置写回 ``os.environ["PATH"]``，让 yt-dlp / bilix 等第三方库
的 ffmpeg 探测（它们内部仍是裸命令）也能找到。

对外：``FFMPEG`` / ``FFPROBE`` 常量、``ffmpeg_bin()`` / ``ffprobe_bin()`` 函数
（缺 PATH 时可重新解析）、``binary_report()`` 诊断信息、``ensure_binaries()``
启动自检（缺失时抛出含安装指引的 RuntimeError）。
"""
from __future__ import annotations

import glob
import logging
import os
import shutil
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

_ENV_BIN = "FRAMES_{name}_BIN"
_ENV_DIR = "FRAMES_FFMPEG_DIR"

# 常见安装目录（按优先级）：macOS Homebrew(arm/x86) → MacPorts → 各 Linux 发行版 → Windows
_COMMON_DIRS: tuple[str, ...] = (
    "/opt/homebrew/bin",
    "/usr/local/bin",
    "/opt/local/bin",
    "/usr/bin",
    "/bin",
    "/snap/bin",
    "/var/lib/flatpak/exports/bin",
    "~/.local/bin",
    "~/bin",
)

_COMMON_DIRS_WINDOWS: tuple[str, ...] = (
    r"C:\ffmpeg\bin",
    r"C:\Program Files\ffmpeg\bin",
    r"C:\ProgramData\chocolatey\bin",
    "~\\scoop\\shims",
)


def _windows_candidates(name: str) -> list[str]:
    """Windows 兜底：winget / scoop 动态目录 + 常见盘符下的 ffmpeg 解压目录。"""
    found: list[str] = []
    local = os.getenv("LOCALAPPDATA") or ""
    if local:
        found += glob.glob(str(Path(local) / "Microsoft" / "WinGet" / "Links" / f"{name}.exe"))
        found += glob.glob(
            str(Path(local) / "Microsoft" / "WinGet" / "Packages" / "*ffmpeg*" / "**" / f"{name}.exe"),
            recursive=True,
        )
    uprofile = os.getenv("USERPROFILE") or ""
    if uprofile:
        found += glob.glob(str(Path(uprofile) / "scoop" / "apps" / "ffmpeg" / "*" / "bin" / f"{name}.exe"))
    for drive in ("C:", "D:", "E:"):
        found += glob.glob(f"{drive}\\ffmpeg*\\bin\\{name}.exe")
    return found


def _search_dirs() -> list[str]:
    dirs = [d for d in _COMMON_DIRS]
    if sys.platform.startswith("win"):
        dirs += list(_COMMON_DIRS_WINDOWS)
    out: list[str] = []
    for d in dirs:
        expanded = str(Path(d).expanduser())
        if expanded not in out:
            out.append(expanded)
    return out


def _resolve(name: str) -> str:
    """解析 ffmpeg/ffprobe 的可用调用路径；找不到时回退裸命令名。"""
    # 1) 显式指定可执行文件
    explicit = (os.getenv(_ENV_BIN.format(name=name.upper())) or "").strip()
    if explicit and Path(explicit).exists():
        return explicit
    # 2) 显式指定 bin 目录
    env_dir = (os.getenv(_ENV_DIR) or "").strip()
    if env_dir:
        for fname in (name, f"{name}.exe"):
            cand = Path(env_dir) / fname
            if cand.exists():
                return str(cand)
    # 3) 当前 PATH
    hit = shutil.which(name)
    if hit:
        return hit
    # 4) 常见安装目录
    exe_names = [name, f"{name}.exe"] if sys.platform.startswith("win") else [name]
    for d in _search_dirs():
        for fname in exe_names:
            cand = Path(d) / fname
            if cand.exists() and os.access(cand, os.X_OK):
                return str(cand)
    # 5) Windows 动态目录（winget/scoop/解压目录）
    if sys.platform.startswith("win"):
        for hit_path in _windows_candidates(name):
            if Path(hit_path).exists():
                return hit_path
    return name


def _augment_path(bins: list[str]) -> None:
    """把已解析到的可执行目录前置进 PATH，供第三方库（yt-dlp/bilix）裸命令探测。"""
    dirs = [str(Path(b).parent) for b in bins if b and (os.sep in b or "/" in b)]
    dirs = [d for d in dict.fromkeys(dirs) if Path(d).is_dir()]
    if not dirs:
        return
    current = os.environ.get("PATH", "")
    parts = current.split(os.pathsep) if current else []
    missing = [d for d in dirs if d not in parts]
    if missing:
        os.environ["PATH"] = os.pathsep.join(missing + parts)


FFMPEG = _resolve("ffmpeg")
FFPROBE = _resolve("ffprobe")
_augment_path([FFMPEG, FFPROBE])

if FFMPEG == "ffmpeg" or FFPROBE == "ffprobe":
    logger.warning(
        "未在 PATH 与常见安装目录中找到 ffmpeg/ffprobe（ffmpeg=%s, ffprobe=%s）；"
        "素材采集（抽帧/转码/时长探测）将失败，请安装：macOS `brew install ffmpeg`，"
        "或在环境变量 FRAMES_FFMPEG_DIR 指定 bin 目录",
        FFMPEG, FFPROBE,
    )
else:
    logger.info("ffmpeg=%s ffprobe=%s", FFMPEG, FFPROBE)


def ffmpeg_bin() -> str:
    """返回 ffmpeg 调用路径（若首次解析失败，此处再尝试一次）。"""
    global FFMPEG
    if FFMPEG == "ffmpeg":
        FFMPEG = _resolve("ffmpeg")
    return FFMPEG


def ffprobe_bin() -> str:
    """返回 ffprobe 调用路径（若首次解析失败，此处再尝试一次）。"""
    global FFPROBE
    if FFPROBE == "ffprobe":
        FFPROBE = _resolve("ffprobe")
    return FFPROBE


def ffmpeg_location() -> str | None:
    """给 yt-dlp 的 ``ffmpeg_location``：已解析到绝对路径时返回其所在目录。

    未解析成功时返回 None（不覆盖 yt-dlp 默认探测行为）。
    """
    ff = ffmpeg_bin()
    if ff == "ffmpeg":
        return None
    parent = Path(ff).parent
    return str(parent) if parent.is_dir() else None


def binary_report() -> dict:
    """诊断信息：供 selfcheck / 启动自检展示（含是否真正可执行）。"""
    ff, fp = ffmpeg_bin(), ffprobe_bin()
    return {
        "ffmpeg": ff,
        "ffprobe": fp,
        "ffmpeg_ok": ff != "ffmpeg" and Path(ff).exists(),
        "ffprobe_ok": fp != "ffprobe" and Path(fp).exists(),
        "path_head": (os.environ.get("PATH", "").split(os.pathsep)[:5]),
    }


class FFmpegMissingError(RuntimeError):
    """ffmpeg/ffprobe 均不可用；错误信息含安装指引，便于用户自助修复。"""


def ensure_binaries() -> None:
    """启动 / 采集前自检：缺失即抛出带安装指引的异常（避免 Errno 2 裸奔）。"""
    rep = binary_report()
    if rep["ffmpeg_ok"] and rep["ffprobe_ok"]:
        return
    missing = [n for n in ("ffmpeg", "ffprobe") if not rep[f"{n}_ok"]]
    raise FFmpegMissingError(
        f"未找到 {'/'.join(missing)}，无法处理音视频。请任选一种方式修复："
        "① macOS: brew install ffmpeg；② Windows: winget install Gyan.FFmpeg；"
        "③ 已有安装包时设置环境变量 FRAMES_FFMPEG_DIR 指向其 bin 目录，"
        "或 FRAMES_FFMPEG_BIN / FRAMES_FFPROBE_BIN 指向可执行文件。"
    )
