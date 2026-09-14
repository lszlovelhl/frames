#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""frames selfcheck v0.1 — 启动前自检（阶段④）

对齐 docs/11-local-packaging.md §7 检查清单与 §8④ 交付要求：
  Python 运行时 / 依赖 import（fastapi·sqlalchemy·aiosqlite 等）/ ffmpeg + ffprobe /
  yt-dlp / faster-whisper 模型目录 / SQLite 库文件（含 WAL）/ 素材目录可写 /
  前端 dist（打包形态）/ 端口占用 / 云薄服务连通性（INFO，不影响退出码）。

退出码：全 PASS=0；有 WARN=1；有 ERROR=2（供启动脚本 / CI / Windows 冒烟判断）。

用法：
    backend/scripts/selfcheck.py                 # 默认读 backend/.env
    backend/scripts/selfcheck.py --json          # 机器可读输出
    backend/scripts/selfcheck.py --port 8123 --skip-net
入口包装：run/selfcheck（macOS/Linux）、run/selfcheck.bat（Windows，待实机冒烟）

仅依赖标准库；被检查方的第三方依赖通过 try-import 探测，本脚本自身不受影响。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
from pathlib import Path

VERSION = "0.1"

OK = "OK"
WARN = "WARN"
ERROR = "ERROR"
INFO = "INFO"

# ---------------------------------------------------------------- 环境与路径


def load_env_file(path: Path) -> dict[str, str]:
    """极简 .env 解析（不覆盖已存在的环境变量），避免依赖 python-dotenv。"""
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    try:
        for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key and key not in os.environ:
                out[key] = val
    except OSError:
        return {}
    return out


def find_backend_dir(explicit: str | None) -> Path:
    """定位 backend 目录：显式参数 > 环境变量 > 从脚本位置向上找 app/main.py。"""
    if explicit:
        return Path(explicit).expanduser().resolve()
    env = os.getenv("FRAMES_BACKEND_DIR")
    if env:
        return Path(env).expanduser().resolve()
    here = Path(__file__).resolve()
    for cand in [here.parent] + list(here.parents):
        if (cand / "app" / "main.py").is_file():
            return cand
    return here.parent.parent  # 兜底：backend/scripts/x.py → backend


def first_line(text: str) -> str:
    for line in text.splitlines():
        line = line.strip()
        if line:
            return line
    return ""


# ------------------------------------------------------------------ 检查项


class Report:
    def __init__(self) -> None:
        self.items: list[dict[str, str]] = []

    def add(self, name: str, status: str, detail: str, hint: str = "") -> None:
        self.items.append(
            {"name": name, "status": status, "detail": detail, "hint": hint}
        )

    def count(self, status: str) -> int:
        return sum(1 for i in self.items if i["status"] == status)

    @property
    def exit_code(self) -> int:
        if self.count(ERROR):
            return 2
        if self.count(WARN):
            return 1
        return 0


def check_python(rep: Report) -> None:
    v = sys.version_info
    detail = f"python {sys.version.split()[0]} ({sys.executable})"
    if v >= (3, 11):
        rep.add("Python 运行时", OK, detail)
    elif v >= (3, 10):
        rep.add("Python 运行时", WARN, detail, "建议 3.11+（包内运行时口径见 docs §5.3）")
    else:
        rep.add("Python 运行时", ERROR, detail, "版本过低，请改用包内 3.11+ 运行时")


def check_deps(rep: Report) -> None:
    required = [
        "fastapi",
        "uvicorn",
        "sqlalchemy",
        "aiosqlite",
        "pydantic",
        "httpx",
        "dotenv",
        "yt_dlp",
    ]
    optional = ["bilix", "faster_whisper"]
    missing: list[str] = []
    for mod in required:
        try:
            __import__(mod)
        except Exception:  # noqa: BLE001
            missing.append(mod)
    opt_missing: list[str] = []
    for mod in optional:
        try:
            __import__(mod)
        except Exception:  # noqa: BLE001
            opt_missing.append(mod)
    if not missing and not opt_missing:
        rep.add("依赖完整性", OK, f"可 import：{'/'.join(required + optional)}")
    elif missing:
        rep.add(
            "依赖完整性",
            ERROR,
            f"缺失必需依赖：{', '.join(missing)}",
            "在 backend 下执行 .venv/bin/pip install -r requirements.txt",
        )
    else:
        rep.add(
            "依赖完整性",
            WARN,
            f"可选依赖缺失：{', '.join(opt_missing)}（对应功能会降级）",
            "如不使用 BGM 分离/内置下载器可忽略",
        )


def find_bin(name: str, backend_dir: Path, sidecar_dir: Path) -> tuple[str | None, str]:
    """查找顺序：PATH → sidecar 目录 → backend/.venv（脚本版包内解释器）。"""
    on_path = shutil.which(name)
    if on_path:
        return on_path, "PATH"
    # 复用运行时解析（app.media.ffbin）：覆盖 Homebrew 等不在 PATH 的常见安装目录，
    # 保证自检结论与运行时实际调用一致（避免"自检报缺失但实际能跑/反之"）
    if name in ("ffmpeg", "ffprobe"):
        try:
            if str(backend_dir) not in sys.path:
                sys.path.insert(0, str(backend_dir))
            from app.media import ffbin as _ffbin

            resolved = _ffbin.ffmpeg_bin() if name == "ffmpeg" else _ffbin.ffprobe_bin()
            if resolved and resolved != name and Path(resolved).is_file():
                return resolved, "安装目录解析"
        except Exception:  # noqa: BLE001 自检不应因解析兜底失败而中断
            pass
    exts = ["", ".exe"] if os.name == "nt" else [""]
    for ext in exts:
        hits = sorted(p for p in sidecar_dir.rglob(f"{name}{ext}") if p.is_file())
        if hits:
            return str(hits[0]), "sidecars"
    venv_bins = [backend_dir / ".venv" / "bin", backend_dir / ".venv" / "Scripts"]
    for vb in venv_bins:
        for ext in exts:
            cand = vb / f"{name}{ext}"
            if cand.is_file():
                return str(cand), ".venv"
    return None, ""


def run_version(cmd: list[str]) -> str:
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=15, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"<执行失败: {exc}>"
    return first_line((proc.stdout or "") + "\n" + (proc.stderr or ""))


def check_ffmpeg(rep: Report, backend_dir: Path, sidecar_dir: Path) -> None:
    for name in ("ffmpeg", "ffprobe"):
        path, origin = find_bin(name, backend_dir, sidecar_dir)
        if not path:
            rep.add(
                f"{name}",
                ERROR,
                "未找到（PATH / sidecars / .venv 均无）",
                f"放入 sidecars/{name}/ 或 brew install ffmpeg",
            )
            continue
        line = run_version([path, "-version"])
        m = re.search(r"version\s+(\S+)", line)
        ver = m.group(1) if m else line
        if "<执行失败" in line:
            rep.add(f"{name}", ERROR, f"{path} 无法执行：{line}", "检查架构匹配与可执行权限")
        else:
            rep.add(f"{name}", OK, f"{ver} ({origin}: {path})")


def check_ytdlp(rep: Report, backend_dir: Path, sidecar_dir: Path) -> None:
    path, origin = find_bin("yt-dlp", backend_dir, sidecar_dir)
    if not path:
        rep.add(
            "yt-dlp",
            ERROR,
            "未找到（PATH / sidecars / .venv 均无）",
            "brew install yt-dlp 或 pip install yt-dlp，或放入 sidecars/yt-dlp/",
        )
        return
    line = run_version([path, "--version"])
    if "<执行失败" in line:
        rep.add("yt-dlp", ERROR, f"{path} 无法执行：{line}", "改用 pip install yt-dlp")
        return
    rep.add("yt-dlp", OK, f"yt-dlp {line} ({origin}: {path})")


def check_whisper(rep: Report, env: dict[str, str], backend_dir: Path) -> None:
    spec = (
        env.get("WHISPER_MODEL_PATH")
        or os.getenv("WHISPER_MODEL_PATH")
        or env.get("FRAMES_WHISPER_MODEL_DIR")
        or os.getenv("FRAMES_WHISPER_MODEL_DIR")
    )
    if spec:
        p = Path(spec).expanduser()
        if p.is_dir() and any(p.glob("*.bin")):
            size_mb = sum(f.stat().st_size for f in p.rglob("*") if f.is_file()) / 1048576
            rep.add("whisper 模型", OK, f"本地 ct2 模型存在 ({size_mb:.0f}MB)：{p}")
        elif p.is_dir():
            rep.add("whisper 模型", WARN, f"目录存在但未见 *.bin：{p}", "确认是否为 ctranslate2 模型目录")
        else:
            rep.add(
                "whisper 模型",
                WARN,
                f"WHISPER_MODEL_PATH 指向的目录不存在：{p}",
                "首次启动将按需下载（约 179MB）或改配 WHISPER_MODEL_PATH",
            )
        return
    hf_hub = Path.home() / ".cache" / "huggingface" / "hub"
    hits = sorted(hf_hub.glob("models--*whisper*")) if hf_hub.is_dir() else []
    if hits:
        rep.add("whisper 模型", OK, f"HF 缓存命中：{hits[0].name}")
    else:
        rep.add(
            "whisper 模型",
            WARN,
            "未配置 WHISPER_MODEL_PATH，HF 缓存未命中",
            "首次转写将按需下载（约 179MB）；离线分发建议外置模型目录",
        )


def check_demucs(rep: Report) -> None:
    try:
        __import__("demucs")
        rep.add("demucs 模型", OK, "demucs 可 import（BGM 分离可用）")
    except Exception:  # noqa: BLE001
        rep.add("demucs 模型", WARN, "未安装（可选，将按需下载）", "BGM 分离暂不可用，其余链路不受影响")


def _probe_writable(directory: Path) -> tuple[bool, str]:
    try:
        directory.mkdir(parents=True, exist_ok=True)
        probe = directory / ".selfcheck_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True, ""
    except OSError as exc:
        return False, str(exc)


def check_database(rep: Report, db_path: Path, data_dir: Path) -> None:
    writable, err = _probe_writable(data_dir)
    if not writable:
        rep.add("数据库文件", ERROR, f"数据目录不可写：{data_dir}（{err}）", "检查目录权限后重试")
        return
    if not db_path.is_file():
        rep.add(
            "数据库文件",
            WARN,
            f"{db_path} 不存在（首次启动将按基线建库）",
            "属正常首启路径；如非首启请检查 DATABASE_URL",
        )
        return
    try:
        conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=5)
        try:
            cur = conn.cursor()
            mode = (cur.execute("PRAGMA journal_mode").fetchone() or ["?"])[0]
            tables = (cur.execute("SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone() or [0])[0]
        finally:
            conn.close()
    except sqlite3.Error as exc:
        rep.add("数据库文件", ERROR, f"打开/读取失败：{exc}", "备份后检查文件完整性，勿直接删库")
        return
    size_mb = db_path.stat().st_size / 1048576
    if str(mode).lower() == "wal":
        rep.add("数据库文件", OK, f"{db_path}（{size_mb:.1f}MB，{tables} 表；WAL=on）")
    else:
        rep.add(
            "数据库文件",
            WARN,
            f"{db_path}（{size_mb:.1f}MB，{tables} 表；journal_mode={mode}）",
            "后端启动时 db.py 会自动开 WAL，无需手工处理",
        )


def check_media(rep: Report, media_dir: Path) -> None:
    ok, err = _probe_writable(media_dir)
    if ok:
        rep.add("素材目录", OK, f"可写：{media_dir}")
    else:
        rep.add("素材目录", ERROR, f"不可写：{media_dir}（{err}）", "检查磁盘空间与目录权限")


def check_frontend(rep: Report, backend_dir: Path) -> None:
    dist = backend_dir.parent / "frontend" / "dist"
    if (dist / "index.html").is_file():
        rep.add("前端 dist", OK, f"已构建：{dist}")
    else:
        rep.add(
            "前端 dist",
            WARN,
            f"未找到 {dist}/index.html",
            "打包形态必需：cd frontend && npm run build（dev 形态可忽略）",
        )


def _listener_pid(port: int) -> str:
    try:
        if os.name == "nt":
            proc = subprocess.run(
                ["netstat", "-ano", "-p", "TCP"], capture_output=True, text=True, timeout=10
            )
            for line in proc.stdout.splitlines():
                if f":{port} " in line and "LISTEN" in line.upper():
                    return line.split()[-1]
        else:
            proc = subprocess.run(
                ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if proc.stdout.strip():
                return proc.stdout.split()[0]
    except Exception:  # noqa: BLE001
        pass
    return ""


def check_port(rep: Report, port: int) -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(2)
    try:
        sock.bind(("127.0.0.1", port))
        sock.listen(1)
        rep.add("端口", OK, f"{port} 空闲可用（127.0.0.1）")
    except OSError as exc:
        pid = _listener_pid(port)
        detail = f"{port} 已被占用" + (f"（PID {pid}）" if pid else "")
        hint = f"先停止占用进程，或改用其他端口启动（{exc.errno}）"
        rep.add("端口", ERROR, detail, hint)
    finally:
        sock.close()


def check_cloud(rep: Report, env: dict[str, str], skip_net: bool) -> None:
    if skip_net:
        rep.add("云薄服务连通性", INFO, "已跳过（--skip-net）")
        return
    url = env.get("FRAMES_CLOUD_URL") or os.getenv("FRAMES_CLOUD_URL")
    if not url:
        rep.add(
            "云薄服务连通性",
            INFO,
            "未配置 FRAMES_CLOUD_URL → 离线模式可用（账号/支付功能将受限）",
        )
        return
    try:
        import urllib.request

        req = urllib.request.Request(url.rstrip("/") + "/health", method="GET")
        with urllib.request.urlopen(req, timeout=3):
            rep.add("云薄服务连通性", INFO, f"可达：{url}")
    except Exception as exc:  # noqa: BLE001
        rep.add("云薄服务连通性", INFO, f"不可达（{type(exc).__name__}）→ 离线模式可用")


# ------------------------------------------------------------------ 输出


def render_text(rep: Report, backend_dir: Path, port: int) -> str:
    lines = [f"frames selfcheck v{VERSION}"]
    width = 22
    for item in rep.items:
        pad = " " * max(1, width - sum(2 if ord(c) > 127 else 1 for c in item["name"]))
        lines.append(f"[{item['status']:<4}] {item['name']}{pad}{item['detail']}")
    bad = [i for i in rep.items if i["status"] in (WARN, ERROR)]
    lines.append("")
    if bad:
        lines.append(f"自检未通过项（WARN {rep.count(WARN)} / ERROR {rep.count(ERROR)}）：")
        for i in bad:
            hint = f" → {i['hint']}" if i["hint"] else ""
            lines.append(f"  - [{i['status']}] {i['name']}：{i['detail']}{hint}")
    else:
        lines.append("自检未通过项：\n（无）")
    lines.append("")
    lines.append(f"退出码：{rep.exit_code}（0=全 PASS / 1=有 WARN / 2=有 ERROR）")
    lines.append(
        f"环境：backend={backend_dir}；端口={port}；"
        f"数据目录={os.getenv('FRAMES_DATA_DIR') or backend_dir / 'data'}"
    )
    lines.append("启动：bash backend/scripts/run_app.sh（阶段②的 run/start 尚未落地）")
    return "\n".join(lines)


def render_json(rep: Report, backend_dir: Path, port: int) -> str:
    payload = {
        "tool": "frames-selfcheck",
        "version": VERSION,
        "backend_dir": str(backend_dir),
        "port": port,
        "checks": rep.items,
        "summary": {
            "ok": rep.count(OK),
            "warn": rep.count(WARN),
            "error": rep.count(ERROR),
            "info": rep.count(INFO),
            "exit_code": rep.exit_code,
        },
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="frames 启动前自检（阶段④）")
    parser.add_argument("--backend-dir", default=None, help="backend 目录（默认自动探测）")
    parser.add_argument("--data-dir", default=None, help="数据目录（默认 <backend>/data）")
    parser.add_argument("--sidecar-dir", default=None, help="sidecar 目录（默认 <data>/sidecars）")
    parser.add_argument("--port", type=int, default=None, help="待检查端口（默认 .env PORT 或 8000）")
    parser.add_argument("--json", action="store_true", help="JSON 输出（供 CI / 冒烟脚本）")
    parser.add_argument("--skip-net", action="store_true", help="跳过云薄服务连通性探测")
    args = parser.parse_args(argv)

    backend_dir = find_backend_dir(args.backend_dir or os.getenv("FRAMES_BACKEND_DIR"))
    env = load_env_file(backend_dir / ".env")
    data_dir = Path(args.data_dir or env.get("FRAMES_DATA_DIR") or backend_dir / "data").expanduser()
    sidecar_dir = Path(args.sidecar_dir or env.get("FRAMES_SIDECAR_DIR") or data_dir / "sidecars").expanduser()
    db_path = Path(env.get("FRAMES_DB_FILE") or data_dir / "frames.db").expanduser()
    media_dir = Path(env.get("FRAMES_MEDIA_DIR") or data_dir / "media").expanduser()
    port = int(args.port or env.get("PORT") or env.get("FRAMES_PORT") or 8000)

    try:  # Windows 控制台（cp936）也能打印中文/符号
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass

    rep = Report()
    check_python(rep)
    check_deps(rep)
    check_ffmpeg(rep, backend_dir, sidecar_dir)
    check_ytdlp(rep, backend_dir, sidecar_dir)
    check_whisper(rep, env, backend_dir)
    check_demucs(rep)
    check_database(rep, db_path, data_dir)
    check_media(rep, media_dir)
    check_frontend(rep, backend_dir)
    check_port(rep, port)
    check_cloud(rep, env, args.skip_net)

    print(render_json(rep, backend_dir, port) if args.json else render_text(rep, backend_dir, port))
    return rep.exit_code


if __name__ == "__main__":
    sys.exit(main())
