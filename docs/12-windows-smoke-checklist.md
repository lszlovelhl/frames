---
AIGC:
    Label: "1"
    ContentProducer: 001191440300708461136T1XGW3
    ProduceID: ce0d790a7b004233c1097246b343160b_7c138c2bacb011f18874525400287e28
    ReservedCode1: zhesyOOXzZdCRuubG2Snf1Kd8lI0wfLUzkCMOep/J+ormx4XUePu9iEUsZN7vqZdhcE2cTssEQ6BpVzUaAgSftAEBOHXtDI2SApHDyFe4r/RwnRx+iM5Ob+aliGreO/n9FI91SfXp0V1NSlhzZxNmjByRhvE0HeHd8CBhJrIlzS62VqgohULvWoEtNA=
    ContentPropagator: 001191440300708461136T1XGW3
    PropagateID: ce0d790a7b004233c1097246b343160b_7c138c2bacb011f18874525400287e28
    ReservedCode2: zhesyOOXzZdCRuubG2Snf1Kd8lI0wfLUzkCMOep/J+ormx4XUePu9iEUsZN7vqZdhcE2cTssEQ6BpVzUaAgSftAEBOHXtDI2SApHDyFe4r/RwnRx+iM5Ob+aliGreO/n9FI91SfXp0V1NSlhzZxNmjByRhvE0HeHd8CBhJrIlzS62VqgohULvWoEtNA=
---



# Windows 冒烟待测清单（阶段④ · Mac 侧已就绪部分）

> 关联文档：[11-local-packaging.md](11-local-packaging.md) §5.4 / §7 / §8④、[13-app-launcher.md](13-app-launcher.md)（类 app 启动体验）、开放问题 Q5
> 编号说明：docs 现有 00–06 + 11 + 12，07–10 为后续文档预留，本篇取 12。
> 定位：Mac 本机已能跑通的检查项，逐条映射为 Windows 实机/VM 上的验证动作；本清单**只列待测项**，不代表已在 Windows 验证通过。

## 0. 执行前提（Q5 待拍板）

| 项 | 说明 |
|---|---|
| 测试环境 | Windows 10/11 实机 或 云 VM（无 GPU 亦可，本链路 CPU 推理） |
| 包形态 | 脚本版（解压即跑）zip，非 PyInstaller/Nuitka（见 §6.2 推荐） |
| 待测包来源 | Mac 侧整理出的 `frames-win/` 目录（backend + frontend/dist + run/ + sidecars/ 占位） |
| 前置依赖 | Windows 侧 Python 运行时（python-build-standalone 3.11+）、ffmpeg/ffprobe/yt-dlp 的 win 版 exe |
| 判定入口 | `run\selfcheck.bat` 退出码：0=全 PASS / 1=有 WARN / 2=有 ERROR |

## 1. 待测清单

| # | 待测项 | 步骤 | 预期结果 | 失败处理方向 | 风险等级 |
|---|---|---|---|---|---|
| W1 | 解压与路径 | 将包解压到含**中文 + 空格**的路径（如 `D:\测试 目录\frames-win\`） | 所有脚本可运行，无乱码 | 路径解析统一用相对包目录（`%~dp0` / `Path(__file__).parents`） | 高（易踩） |
| W2 | Python 运行时 | `run\selfcheck.bat` 第 1 项 | `[OK] Python 运行时 python 3.11.x (...\runtime\python)` | 换 python-build-standalone，勿依赖系统 Python | 中 |
| W3 | 依赖完整性 | 同上第 2 项 | `[OK] 依赖完整性 可 import：fastapi/uvicorn/sqlalchemy/aiosqlite/pydantic/httpx/dotenv/yt_dlp/bilix/faster_whisper` | 用 `runtime\python -m pip install -r backend\requirements.txt` 重装；若缺 `bilix/faster_whisper` 仅降级为 WARN | 高 |
| W4 | 控制台编码 | 在 `cmd.exe`（默认 GBK）与 Windows Terminal（UTF-8）各跑一次 | 中文与 `[OK]/[WARN]` 对齐正常，无 `UnicodeEncodeError` | selfcheck.py 已 `stdout.reconfigure(utf-8)`；必要时 `chcp 65001` | 中 |
| W5 | ffmpeg / ffprobe | 把 win 版 exe 放入 `sidecars\ffmpeg\`，**不加入 PATH**，再跑 selfcheck | `[OK] ffmpeg 8.x (sidecars: ...)`、`[OK] ffprobe` | 确认 exe 具备执行权限且不是 0 字节；架构 x64 与系统匹配 | 中 |
| W6 | yt-dlp | 把 `yt-dlp.exe` 放入 `sidecars\yt-dlp\`，或不放而用包内 venv（`backend\.venv\Scripts\yt-dlp.exe`） | `[OK] yt-dlp yt-dlp 2026.x`；`prepare_sidecars` 等价检查无 MISS | 仍 MISS 时确认查找顺序 PATH → sidecars → `.venv\Scripts` | 中 |
| W7 | sidecar 预检脚本 | `bash backend/scripts/prepare_sidecars.sh`（Git Bash / WSL） | `== 预检完成：全部就绪 ==`（退出码 0） | **Windows 原生尚无 `.ps1` 版本 → 记为待补产物**（见 §3） | 中 |
| W8 | whisper 模型 | 设 `WHISPER_MODEL_PATH=D:\...\sidecars\models\faster-whisper-small`（复用 Mac 侧同款 ct2 目录） | `[OK] whisper 模型 本地 ct2 模型存在 (xxxMB)` | 不放模型则允许 `[WARN]` + 首次联网下载（179MB） | 中 |
| W9 | 数据目录 | 不设任何环境变量直接启动，确认落盘位置 | 库与素材写入 `%LOCALAPPDATA%\frames\`（或包内 `backend\data\`，以拍板口径为准），**不写包外系统目录** | 统一由 config 解析；避免写 `Program Files`（无权限） | 高 |
| W10 | SQLite 库与 WAL | selfcheck `数据库文件` 项；启动后再看一次 | 首次启动自动建库；`WAL=on`；22 表 | 目录不可写时给出权限指引，不自动删库 | 中 |
| W11 | 素材目录可写 | selfcheck `素材目录` 项 | `[OK] 可写` | 检查磁盘空间 / 杀软拦截 | 低 |
| W12 | 端口检查 | 先占用 8000（起一个临时服务）再跑 selfcheck；然后释放重跑 | 占用时 `[ERROR] 8000 已被占用（PID xxxx）` 且退出码 2；释放后 `[OK]` | PID 探测 Windows 走 `netstat -ano`；如被安全软件挡住则仅报占用不报 PID | 中 |
| W13 | 前端静态托管 | 浏览器打开 `http://127.0.0.1:8000/` | 首页正常、无 CORS/双端口问题 | 确认 `frontend\dist` 随包且 `index.html` 存在 | 中 |
| W14 | 一键启动 | 双击 `run\启动帧间.bat`（终端等价：`python backend\scripts\launcher.py start`） | 免配置拉起后端、托管 dist、自动打开浏览器 | 闪退时改用终端命令查看完整报错；确认 `.venv\Scripts\python.exe` 或 PATH 上的 python | 高 |
| W15 | 核心路径闭环 | 建档 → 下载（yt-dlp）→ 拆解 → auto 清理 → 回放 | 与 Mac 侧 A 闭环一致：拆解 done 触发清理、`video_url=null` 时前端降级组件可回源 | 关注 Windows 下路径分隔符与 ffmpeg 调用参数 | 高 |
| W16 | 杀软与首次运行 | Defender/第三方安全软件开启状态下解压运行 | 无静默拦截（脚本版无签名 exe，误报面小） | 如被拦截，记录拦截文件名与提示，作为交付说明素材 | 中 |
| W17 | 时区/日期戳 | 检查日志与 `cleaned_at` 等时间字段 | 与 UTC 存库口径一致，展示层本地化 | 复用既有 UTC 约定 | 低 |
| W18 | 中文文件名素材 | 上传/生成含中文名的视频与音频 | 抽帧、音轨、manifest 均正常 | 编码统一 UTF-8 + `pathlib` | 中 |
| W19 | 双击启动入口 | 双击 `run\启动帧间.bat` | 窗口打印检查项 → `服务就绪 http://127.0.0.1:8000/` + 自动打开系统默认浏览器；**关闭窗口后服务仍在**（再执行 `status` 可查） | 闪退时用 `python backend\scripts\launcher.py start` 看完整报错；确认解释器路径 | 高 |
| W20 | 双击停止入口 | 双击 `run\停止帧间.bat` | `停止完成  端口 8000 已释放`；随后可再次双击启动 | 端口未释放时会报占用者 PID，到任务管理器核对 | 中 |
| W21 | 防重入与多实例 | 已启动状态下再双击一次「启动帧间」；另用 `--port 8081` 起第二实例 | 第二次报 `已在运行 PID=xxxx` 不重复拉起；8081 实例与 8000 实例互不干扰（PID/日志分文件） | 若误报占用，检查 `backend\data\uvicorn.pid` 是否为失效 PID（启动器会自动清理） | 中 |
| W22 | 中文/空格路径与控制台编码 | 包解压到 `D:\测试 目录\frames-win\` 后双击启动 | 输出中文正常、无 `UnicodeEncodeError`；`chcp 65001` 生效 | 入口已设 `PYTHONIOENCODING=utf-8` 并 `chcp 65001`；仍乱码时记录控制台类型 | 中 |

## 2. 冒烟记录表（执行时填写）

| 编号 | 结果（PASS/FAIL/WARN） | 实际输出摘要 | 备注 |
|---|---|---|---|
| W1 |  |  |  |
| W5 |  |  |  |
| W6 |  |  |  |
| W9 |  |  |  |
| W12 |  |  |  |
| W13 |  |  |  |
| W14 |  |  |  |
| W15 |  |  |  |
| W19 |  |  |  |
| W20 |  |  |  |
| W21 |  |  |  |
| … |  |  |  |

## 3. 待补产物（Windows 侧尚未落地）

| 产物 | 目的 | 参考实现 |
|---|---|---|
| `backend/scripts/prepare_sidecars.ps1` | 原生 PowerShell 版 sidecar 预检 | `prepare_sidecars.sh` 的查找顺序：PATH → sidecars → `.venv\Scripts` |
| `run/selfcheck.bat` | 自检入口 | 已随早前改动落地，**但未在 Windows 实机验证** |
| `run/启动帧间.bat` / `run/停止帧间.bat` | 双击启停入口 | 已随本次改动落地（包装 `backend/scripts/launcher.py`），**但未在 Windows 实机验证** |
| `runtime/python`（解释器） | 免装 Python | python-build-standalone 3.11+ win x64 |
| `sidecars/{ffmpeg,yt-dlp}` win exe | 素材链路 | ffmpeg: gyan.dev / BtbN；yt-dlp: 官方 Releases |
| （已落地，无需再补）`run/start.bat` 等价物 | 一键启动（含防重入） | 由 `run/启动帧间.bat` + `backend/scripts/launcher.py` 覆盖 |

## 4. Mac 侧已就绪（本次交付，Windows 待对照）

| 项 | 状态 |
|---|---|
| `backend/scripts/selfcheck.py`（跨平台，纯标准库） | Mac 实测通过：端口空闲时退出码 1（仅 demucs 可选 WARN）、端口占用时 2（含 PID）、`--json` 正常 |
| `run/selfcheck` / `run/selfcheck.bat` | macOS 已验证；Windows 版待冒烟 |
| `backend/scripts/prepare_sidecars.sh` 预检 | Mac 全绿（ffmpeg/ffprobe/yt-dlp 均 OK，退出码 0） |
| `backend/requirements.txt` | 已补 `httpx / yt-dlp / bilix / faster-whisper`，Windows 装依赖可直接复用 |
| `backend/app/main.py` 单端口托管 | Mac 实测：`/` 即完整界面、前端深链回退 200、`/api/*` 未被吞、dist 缺失时降级为仅 API（见 [13-app-launcher.md](13-app-launcher.md) §5） |
| `backend/scripts/launcher.py`（跨平台，纯标准库） | Mac 实测通过：`start`/`stop`/`status`/`restart`、依赖与端口检查、健康等待、防重入、按端口隔离 PID、`--json` |
| `run/start` / `run/stop` / `run/status` | macOS 已验证（自选解释器 + 透传参数） |
| `run/启动帧间.command` / `run/停止帧间.command` | macOS 已验证（双击链路、端口解析、浏览器调用、退出码） |

*（内容由AI生成，仅供参考）*
*（内容由AI生成，仅供参考）*
