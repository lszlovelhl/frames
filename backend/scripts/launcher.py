#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""帧间（Frames）跨平台启动器 — 纯标准库实现

像用 app 一样启动：双击 `run/启动帧间.command`（macOS）或 `run/启动帧间.bat`（Windows），
或命令行 `python backend/scripts/launcher.py start`。

子命令：
    start   依赖检查 → 端口检查 → 后台拉起后端 → 健康等待 → 自动打开浏览器
    stop    优雅停止（先 TERM 后强制），并等端口释放
    status  查看运行状态（PID / 端口 / 健康 / URL / 日志）
    restart stop + start

常用参数：
    --port N        监听端口（默认 8000，或环境变量 FRAMES_PORT）
    --no-browser    不自动打开浏览器（环境变量 FRAMES_OPEN_BROWSER=0 等效）
    --timeout N     健康等待超时秒数（默认 60）
    --json          以 JSON 输出结果（供脚本/自检消费）

设计约束：不额外依赖任何三方包；不写系统目录；日志与 PID 落在 backend/data 下，
与 backend/scripts/run_app.sh、stop_app.sh 共用同一 PID 文件（data/uvicorn.pid）。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from datetime import datetime, timezone
from pathlib import Path

VERSION = "1.0"
IS_WINDOWS = os.name == "nt"

# 控制台编码兜底：Windows GBK 控制台也能打印中文与路径
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass

BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_DIR = BACKEND_DIR.parent
DATA_DIR = Path(os.getenv("FRAMES_DATA_DIR") or (BACKEND_DIR / "data"))
LOG_DIR = DATA_DIR / "logs"
DEFAULT_PORT = 8000
# 默认端口沿用 data/uvicorn.pid，与既有 run_app.sh / stop_app.sh 互通；
# 非默认端口使用 data/uvicorn-<port>.pid，互不干扰（便于多端口并存与实测）。
PID_FILE = DATA_DIR / "uvicorn.pid"  # 纯数字 PID，与既有 shell 脚本互通
META_FILE = DATA_DIR / "uvicorn.meta.json"  # 附加元信息（端口/启动时间）


def runtime_files(port: int) -> tuple[Path, Path, Path]:
    """按端口返回 (pid 文件, 元信息文件, 日志文件)。"""
    if int(port) == DEFAULT_PORT:
        return PID_FILE, META_FILE, LOG_DIR / "uvicorn.log"
    return (
        DATA_DIR / f"uvicorn-{port}.pid",
        DATA_DIR / f"uvicorn-{port}.meta.json",
        LOG_DIR / f"uvicorn-{port}.log",
    )



# --------------------------------------------------------------------------- #
# 输出
# --------------------------------------------------------------------------- #
def say(tag: str, name: str, detail: str = "") -> None:
    line = f"[{tag:<5}] {name}"
    if detail:
        line += f"  {detail}"
    print(line, flush=True)


# --------------------------------------------------------------------------- #
# 基础工具
# --------------------------------------------------------------------------- #
def find_python() -> str:
    """优先包内虚拟环境解释器（打包场景），否则用当前解释器。"""
    candidates = [
        BACKEND_DIR / ".venv" / ("Scripts/python.exe" if IS_WINDOWS else "bin/python"),
        BACKEND_DIR / ".venv" / ("Scripts/python.exe" if IS_WINDOWS else "bin/python3"),
    ]
    for c in candidates:
        if c.exists():
            return str(c)
    return sys.executable


def port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if IS_WINDOWS:
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        exit_code = ctypes.c_ulong()
        ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
        kernel32.CloseHandle(handle)
        return bool(ok) and exit_code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def read_pid(port: int) -> int | None:
    pid_file, _, _ = runtime_files(port)
    try:
        raw = pid_file.read_text(encoding="utf-8").strip()
        return int(raw) if raw else None
    except Exception:  # noqa: BLE001
        return None


def read_meta(port: int) -> dict:
    _, meta_file, _ = runtime_files(port)
    try:
        return json.loads(meta_file.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def write_meta(pid: int, port: int) -> None:
    _, meta_file, log_file = runtime_files(port)
    meta_file.write_text(
        json.dumps(
            {
                "pid": pid,
                "port": port,
                "python": find_python(),
                "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "log": str(log_file),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def clear_runtime_files(port: int) -> None:
    pid_file, meta_file, _ = runtime_files(port)
    for f in (pid_file, meta_file):
        try:
            f.unlink()
        except FileNotFoundError:
            pass
        except Exception:  # noqa: BLE001
            pass


def listening_pid(port: int) -> int | None:
    """尽力探测端口占用者 PID（无 lsof/netstat 时返回 None）。"""
    try:
        if IS_WINDOWS:
            out = subprocess.run(
                ["netstat", "-ano"], capture_output=True, text=True, timeout=8
            ).stdout
            for line in out.splitlines():
                if f":{port} " in line and "LISTENING" in line.upper():
                    return int(line.split()[-1])
            return None
        lsof = shutil.which("lsof")
        if not lsof:
            return None
        out = subprocess.run(
            [lsof, "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
            capture_output=True,
            text=True,
            timeout=8,
        ).stdout.strip()
        return int(out.splitlines()[0]) if out else None
    except Exception:  # noqa: BLE001
        return None


def http_probe(url: str, timeout: float = 1.5) -> tuple[int, bytes]:
    req = urllib.request.Request(url, headers={"User-Agent": f"frames-launcher/{VERSION}"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, resp.read(4096)


def is_healthy(port: int) -> bool:
    try:
        status, _ = http_probe(f"http://127.0.0.1:{port}/")
        return status == 200
    except Exception:  # noqa: BLE001
        return False


def check_deps(python: str) -> tuple[bool, str]:
    code = "import fastapi, uvicorn, sqlalchemy, aiosqlite"
    try:
        r = subprocess.run(
            [python, "-c", code], capture_output=True, text=True, timeout=60
        )
    except Exception as exc:  # noqa: BLE001
        return False, f"无法执行解释器：{exc}"
    if r.returncode == 0:
        return True, "fastapi / uvicorn / sqlalchemy / aiosqlite 就绪"
    return False, (r.stderr.strip().splitlines() or ["依赖缺失"])[-1]


def web_ui_path() -> Path:
    return Path(
        os.getenv("FRONTEND_DIST")
        or str(PROJECT_DIR / "frontend" / "dist")
    )


# --------------------------------------------------------------------------- #
# 子命令
# --------------------------------------------------------------------------- #
def cmd_start(args) -> int:
    port = args.port
    result: dict = {"action": "start", "port": port, "ok": False}
    pid_file, _meta_file, log_file = runtime_files(port)
    say("INFO", f"帧间启动器 v{VERSION}", f"端口 {port}")

    # 1) 已在运行？—— 幂等：直接打开界面，不再重复拉起
    old_pid = read_pid(port)
    if old_pid and pid_alive(old_pid):
        url = f"http://127.0.0.1:{port}/"
        if is_healthy(port):
            say("OK", "已在运行", f"PID={old_pid}  {url}")
            if args.open_browser:
                webbrowser.open(url)
                say("OK", "浏览器", "已打开（未重复启动服务）")
            result.update(ok=True, pid=old_pid, url=url, already_running=True)
            return finish(args, result)
        say("WARN", "进程存活但服务未响应", f"PID={old_pid}；可先 stop 再 start")
    elif old_pid:
        say("WARN", "发现失效 PID 文件", f"旧 PID={old_pid}，已清理")
        clear_runtime_files(port)

    # 2) 依赖检查
    python = find_python()
    ok, detail = check_deps(python)
    if ok:
        say("OK", "依赖检查", detail)
    else:
        say("ERROR", "依赖检查", detail)
        say("INFO", "修复建议", f"{python} -m pip install -r {BACKEND_DIR/'requirements.txt'}")
        result["error"] = "missing_deps"
        return finish(args, result)
    result["python"] = python

    # 3) 端口检查
    if not port_free(port):
        holder = listening_pid(port)
        say(
            "ERROR",
            "端口被占用",
            f"{port}" + (f"  占用进程 PID={holder}" if holder else ""),
        )
        say("INFO", "修复建议", f"改用其他端口：--port {port + 1}，或先关闭占用进程")
        result["error"] = "port_in_use"
        return finish(args, result)
    say("OK", "端口", f"{port} 可用（127.0.0.1）")

    # 4) 前端界面
    dist = web_ui_path()
    if (dist / "index.html").is_file():
        say("OK", "前端界面", f"{dist}（单端口托管，根路径即完整界面）")
        result["web_ui"] = True
    else:
        say("WARN", "前端界面", f"未找到 {dist}/index.html，将以「仅 API」模式启动")
        result["web_ui"] = False

    # 5) 后台拉起
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env.setdefault("PYTHONUNBUFFERED", "1")
    cmd = [
        python,
        "-m",
        "uvicorn",
        "app.main:app",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
    ]
    if IS_WINDOWS:
        creationflags = (
            getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
            | getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        )
        popen_kwargs: dict = {"creationflags": creationflags}
    else:
        popen_kwargs = {"start_new_session": True}

    log_handle = open(log_file, "ab", buffering=0)
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(BACKEND_DIR),
            env=env,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            **popen_kwargs,
        )
    except Exception as exc:  # noqa: BLE001
        say("ERROR", "启动失败", str(exc))
        result["error"] = str(exc)
        return finish(args, result)
    finally:
        try:
            log_handle.close()
        except Exception:  # noqa: BLE001
            pass

    pid_file.parent.mkdir(parents=True, exist_ok=True)
    pid_file.write_text(str(proc.pid), encoding="utf-8")
    write_meta(proc.pid, port)
    result["pid"] = proc.pid
    say("OK", "已拉起进程", f"PID={proc.pid}  日志 {log_file}")

    # 6) 健康等待
    started = time.time()
    deadline = started + args.timeout
    healthy = False
    printed = False
    while time.time() < deadline:
        if proc.poll() is not None:
            break
        if is_healthy(port):
            healthy = True
            break
        if not printed:
            print(f"[    ] 等待服务就绪（最多 {args.timeout}s）…", flush=True)
            printed = True
        time.sleep(0.4)

    if not healthy:
        say("ERROR", "服务未就绪", "进程已退出或健康检查超时")
        tail_log(12, port)
        try:
            if proc.poll() is None:
                terminate(proc.pid)
        except Exception:  # noqa: BLE001
            pass
        clear_runtime_files(port)
        result["error"] = "unhealthy"
        return finish(args, result)

    url = f"http://127.0.0.1:{port}/"
    elapsed = time.time() - started
    say("OK", "服务就绪", f"{url}（耗时 {elapsed:.1f}s）")
    result.update(ok=True, url=url)
    if args.open_browser:
        try:
            webbrowser.open(url)
            say("OK", "浏览器", "已打开界面")
        except Exception as exc:  # noqa: BLE001
            say("WARN", "浏览器", f"自动打开失败：{exc}；请手动访问 {url}")
    say("INFO", "停止方式", "双击「停止帧间」或执行 run/stop")
    return finish(args, result)


def cmd_stop(args) -> int:
    port = args.port
    result: dict = {"action": "stop", "port": port, "ok": False}
    pid = read_pid(port)
    if not pid:
        if not port_free(port):
            holder = listening_pid(port)
            say(
                "WARN",
                "无 PID 记录但端口被占用",
                f"{port}" + (f"  PID={holder}" if holder else ""),
            )
            result["error"] = "unknown_owner"
        else:
            say("OK", "未在运行", f"端口 {port} 空闲")
            result["ok"] = True
        return finish(args, result)

    if not pid_alive(pid):
        say("WARN", "进程已不存在", f"清理失效 PID 文件（旧 PID={pid}）")
        clear_runtime_files(port)
        result["ok"] = True
        return finish(args, result)

    say("INFO", "正在停止", f"PID={pid}")
    exit_code = terminate(pid)
    # 等端口释放
    deadline = time.time() + 15
    while time.time() < deadline and not port_free(port):
        time.sleep(0.3)
    freed = port_free(port)
    clear_runtime_files(port)
    say("OK" if freed else "WARN", "停止完成", f"端口 {port} " + ("已释放" if freed else "仍被占用"))
    result.update(ok=freed, exit_code=exit_code, port_freed=freed)
    return finish(args, result)


def cmd_status(args) -> int:
    port = args.port
    pid = read_pid(port)
    meta = read_meta(port)
    port = int(meta.get("port") or port)
    _, _, log_file = runtime_files(port)
    alive = bool(pid and pid_alive(pid))
    healthy = is_healthy(port) if alive else False
    result = {
        "action": "status",
        "running": alive and healthy,
        "pid": pid if alive else None,
        "port": port,
        "healthy": healthy,
        "url": f"http://127.0.0.1:{port}/" if healthy else None,
        "started_at": meta.get("started_at"),
        "log": str(log_file),
        "ok": alive and healthy,
    }
    if alive and healthy:
        say("OK", "运行中", f"PID={pid}  端口 {port}")
        say("OK", "访问地址", f"http://127.0.0.1:{port}/")
        if meta.get("started_at"):
            say("INFO", "启动时间", str(meta["started_at"]))
        say("INFO", "日志", str(log_file))
    elif alive:
        say("WARN", "进程存活但服务未响应", f"PID={pid}  端口 {port}")
        tail_log(8, port)
    else:
        say("INFO", "未在运行", f"端口 {port} 空闲" if port_free(port) else f"端口 {port} 被其他进程占用")
    return finish(args, result)


def cmd_restart(args) -> int:
    cmd_stop(args)
    time.sleep(0.5)
    return cmd_start(args)


# --------------------------------------------------------------------------- #
# 停止进程 / 日志 / 收尾
# --------------------------------------------------------------------------- #
def terminate(pid: int, grace: float = 12.0) -> int | None:
    """先优雅后强制停止；返回退出码（无法获取时为 None）。"""
    if IS_WINDOWS:
        subprocess.run(["taskkill", "/PID", str(pid), "/T"], capture_output=True)
        deadline = time.time() + grace
        while time.time() < deadline and pid_alive(pid):
            time.sleep(0.3)
        if pid_alive(pid):
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True
            )
            time.sleep(0.5)
        return None
    try:
        os.kill(pid, 15)  # SIGTERM
    except ProcessLookupError:
        return None
    deadline = time.time() + grace
    while time.time() < deadline and pid_alive(pid):
        time.sleep(0.3)
    if pid_alive(pid):
        try:
            os.kill(pid, 9)  # SIGKILL
        except ProcessLookupError:
            pass
        time.sleep(0.3)
    return 0


def tail_log(lines: int = 12, port: int = DEFAULT_PORT) -> None:
    _, _, log_file = runtime_files(port)
    try:
        content = log_file.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:  # noqa: BLE001
        return
    if not content:
        return
    print(f"[    ] 最近日志（{log_file}）：", flush=True)
    for line in content[-lines:]:
        print(f"       {line}", flush=True)


def finish(args, result: dict) -> int:
    if getattr(args, "json", False):
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="launcher.py", description="帧间（Frames）跨平台启动器"
    )
    p.add_argument("command", choices=["start", "stop", "status", "restart"])
    p.add_argument(
        "--port",
        type=int,
        default=None,
        help="监听端口（默认 8000；环境变量 FRAMES_PORT，或上一次启动记录的端口）",
    )
    p.add_argument(
        "--no-browser",
        dest="open_browser",
        action="store_false",
        default=os.getenv("FRAMES_OPEN_BROWSER", "1") not in ("0", "false", "no"),
        help="不自动打开浏览器",
    )
    p.add_argument(
        "--open-browser",
        dest="open_browser",
        action="store_true",
        help="启动完成后自动打开浏览器（默认行为）",
    )
    p.add_argument("--timeout", type=int, default=60, help="健康等待超时秒数（默认 60）")
    p.add_argument("--json", action="store_true", help="以 JSON 输出结果")
    p.add_argument("--version", action="version", version=f"frames-launcher {VERSION}")
    return p


def resolve_port(args) -> int:
    """端口优先级：--port > FRAMES_PORT > 上次启动记录 > 8000。"""
    if args.port:
        return int(args.port)
    env = os.getenv("FRAMES_PORT")
    if env and env.strip().isdigit():
        return int(env)
    meta_port = read_meta(DEFAULT_PORT).get("port")
    if isinstance(meta_port, int):
        return meta_port
    return 8000


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    args.port = resolve_port(args)
    try:
        return {
            "start": cmd_start,
            "stop": cmd_stop,
            "status": cmd_status,
            "restart": cmd_restart,
        }[args.command](args)
    except KeyboardInterrupt:
        say("WARN", "已中断", "用户取消")
        return 130


if __name__ == "__main__":
    sys.exit(main())
