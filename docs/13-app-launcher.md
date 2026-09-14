---
AIGC:
    Label: "1"
    ContentProducer: 001191440300708461136T1XGW3
    ProduceID: ce0d790a7b004233c1097246b343160b_348339f6ace311f18039525400461939
    ReservedCode1: LLaLMuYr8hLSWWh0cAWjATP5l6CD86z/BMejkBDtgZzwSZgRoDWojSxSZggtAzzR1DS0c19ckyR/GoS9h2XdZrmQh4gaw+PH3hIdpclm+vNHiMkMuzhAv59ZbOTtPbbgywj/sWuswhYIfWhe9Ts8gOk6m+U0dOT+UX2mUnowWW5xQ/9HxL/e1eYJa28=
    ContentPropagator: 001191440300708461136T1XGW3
    PropagateID: ce0d790a7b004233c1097246b343160b_348339f6ace311f18039525400461939
    ReservedCode2: LLaLMuYr8hLSWWh0cAWjATP5l6CD86z/BMejkBDtgZzwSZgRoDWojSxSZggtAzzR1DS0c19ckyR/GoS9h2XdZrmQh4gaw+PH3hIdpclm+vNHiMkMuzhAv59ZbOTtPbbgywj/sWuswhYIfWhe9Ts8gOk6m+U0dOT+UX2mUnowWW5xQ/9HxL/e1eYJa28=
---

# 类 app 启动体验（单端口托管 + 跨平台启动器）

> 关联文档：[11-local-packaging.md](11-local-packaging.md)（打包方案）、[12-windows-smoke-checklist.md](12-windows-smoke-checklist.md)（Windows 待测）
> 编号说明：docs 现有 00–06 + 11 + 12，07–10 预留，本篇取 13。
> 目标：让「帧间」在 macOS 与 Windows 上都能**像用 app 一样**启动——双击一个入口就打开界面，关掉窗口服务仍在后台，随时双击另一个入口停止。

## 1. 交付物

| 文件 | 平台 | 作用 |
|---|---|---|
| `backend/scripts/launcher.py` | 跨平台（纯标准库） | 启动器内核：`start` / `stop` / `status` / `restart` |
| `run/start` · `run/stop` · `run/status` | macOS / Linux | 终端入口（自动选 `.venv` 解释器，回退 `python3`） |
| `run/启动帧间.command` | macOS | **双击启动**，启动后自动打开浏览器 |
| `run/停止帧间.command` | macOS | **双击停止** |
| `run/启动帧间.bat` | Windows | **双击启动**，启动后自动打开浏览器 |
| `run/停止帧间.bat` | Windows | **双击停止** |
| `backend/app/main.py` | 跨平台 | 新增前端 `dist` 单端口托管（缺失时降级为仅 API） |

Windows 的 4 个入口已随包交付，但**尚未在 Windows 实机验证**，见 [12-windows-smoke-checklist.md](12-windows-smoke-checklist.md) 的 W19–W22。

## 2. 怎么用

### 2.1 双击（推荐给日常使用）

| 平台 | 启动 | 停止 |
|---|---|---|
| macOS | 双击 `run/启动帧间.command` | 双击 `run/停止帧间.command` |
| Windows | 双击 `run/启动帧间.bat` | 双击 `run/停止帧间.bat` |

双击后会出现一个终端窗口，打印检查结果；服务在后台运行，**窗口可直接关闭**（关闭窗口不会停止服务）。

- macOS 首次双击若提示「无法打开，因为来自身份不明的开发者」：右键 → 打开，或到「系统设置 → 隐私与安全性」允许。也可先在终端执行一次 `chmod +x "run/启动帧间.command"`。
- 启动成功后会自动用系统默认浏览器打开 `http://127.0.0.1:8000/`。

### 2.2 终端

```bash
# macOS
run/start                     # 启动（默认 8000，自动开浏览器）
run/start --port 8080         # 指定端口
run/start --no-browser        # 不开浏览器
run/status                    # 查看状态（0=运行中，1=未运行）
run/stop                      # 停止
run/restart                   # 重启

# Windows（cmd / PowerShell）
python backend\scripts\launcher.py start
python backend\scripts\launcher.py stop
python backend\scripts\launcher.py status --json
```

`--json` 输出机器可读结果，便于脚本与自检消费。

## 3. 单端口托管（一个地址即完整界面）

- 后端启动时检测 `<项目根>/frontend/dist/index.html`：
  - **存在** → 把 `dist` 挂到根路径 `/`，`http://127.0.0.1:<端口>/` 直接是完整界面，不再需要另起前端 dev server（也就没有双端口 / CORS 问题）。
  - **不存在** → 优雅降级为「仅 API」：根路径返回 JSON 状态（`web_ui: false`）并给出提示，日志打印 `已降级为仅 API 模式`，其余 API 照常可用。
- 前端路由回退由 `SpaStaticFiles` 负责：未命中的路径（如 `/videos/123` 刷新页面）回退 `index.html`，不会 404。
- **路由顺序红线**：前端挂载放在**所有 API 路由之后**注册，否则 `mount("/")` 会吞掉 `/api/*`。`/api/health/db` 等接口必须注册在挂载之前（本次修复了该顺序问题，实测 `/api/health/db` 返回 JSON）。
- 可用环境变量 `FRONTEND_DIST` 覆盖 dist 目录（打包裁剪、灰度替换 dist 时用）。

## 4. 启动器行为

`start` 的顺序：**已在运行？→ 依赖检查 → 端口检查 → 前端产物检查 → 后台拉起 → 健康等待 → 打开浏览器**。

| 能力 | 说明 |
|---|---|
| 幂等（防重入） | 依据 PID 文件判断：已在运行且健康则直接打开浏览器并退出，不重复拉起 |
| 依赖检查 | 用目标解释器 `import fastapi, uvicorn, sqlalchemy, aiosqlite`，失败时给出 `pip install -r backend/requirements.txt` 指引 |
| 端口检查 | 先做 bind 探测；被占用时报出占用者 PID（macOS 用 `lsof`，Windows 用 `netstat -ano`，取不到则只报占用） |
| 健康等待 | 轮询 `GET http://127.0.0.1:<端口>/` 直到 200，超时（默认 60s）则打印日志尾部并回收进程 |
| 自动开浏览器 | 使用系统默认浏览器；Windows 双击入口内先 `chcp 65001` 并设 `PYTHONIOENCODING=utf-8`，避免中文乱码 |
| 后台运行 | macOS `start_new_session`，Windows `DETACHED_PROCESS + CREATE_NEW_PROCESS_GROUP + CREATE_NO_WINDOW`，关闭终端不影响服务 |
| 日志 | 追加写入 `backend/data/logs/uvicorn.log`（非默认端口为 `uvicorn-<端口>.log`） |

`stop` 的顺序：读 PID → 校验存活 → 先优雅（macOS `SIGTERM` / Windows `taskkill /T`）→ 12s 未退出升级为强制（`SIGKILL` / `taskkill /T /F`）→ 最多等 15s 确认端口释放 → 清理 PID 与元信息文件。端口没释放时如实报告并保留告警，不谎报成功。

`status` 输出 PID / 端口 / 健康状态 / 访问地址 / 启动时间 / 日志路径；退出码 0=运行中且健康，1=未运行或未就绪。

### 4.1 运行时文件与端口约定

| 端口 | PID 文件 | 元信息 | 日志 |
|---|---|---|---|
| 8000（默认） | `backend/data/uvicorn.pid` | `uvicorn.meta.json` | `logs/uvicorn.log` |
| 其他端口 | `backend/data/uvicorn-<端口>.pid` | `uvicorn-<端口>.meta.json` | `logs/uvicorn-<端口>.log` |

- 默认端口沿用既有 `backend/scripts/run_app.sh` / `stop_app.sh` 的 `data/uvicorn.pid`，两套入口**可互相启停**（launcher 读同一个 PID 文件）。
- 非默认端口使用独立文件，多个实例（含实测）互不干扰，不会覆盖正在运行服务的 PID 记录。

### 4.2 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `FRAMES_PORT` | `8000` | 默认端口（命令行 `--port` 优先） |
| `FRAMES_OPEN_BROWSER` | `1` | 设 `0` 可关闭自动开浏览器（双击入口显式传 `--open-browser`，不受此影响） |
| `FRONTEND_DIST` | `<项目根>/frontend/dist` | 前端构建产物目录 |
| `FRAMES_DATA_DIR` | `backend/data` | 数据目录（PID / 日志 / 库） |

## 5. macOS 本机实测记录

测试环境：macOS，`backend/.venv/bin/python` 3.11.9。全部测试使用**空闲端口**（8123 / 8124 / 8126），未触碰 8000 上正在运行的服务。

| # | 场景 | 命令 | 结果 | 退出码 |
|---|---|---|---|---|
| 1 | 初始状态 | `run/status --port 8123` | `未在运行  端口 8123 空闲` | 1 |
| 2 | 启动 | `run/start --port 8123 --no-browser` | 依赖 OK → 端口 OK → 前端 OK → PID 9847 → 就绪耗时 2.5s | 0 |
| 3 | 单端口界面 | `curl http://127.0.0.1:8123/` | 200 `text/html`，返回 `dist/index.html` 内容 | — |
| 4 | 前端深链 | `curl http://127.0.0.1:8123/deep/route` | 200 `text/html`（回退 index.html，不 404） | — |
| 5 | 静态资源 | `curl .../assets/index-*.js` | 200 `text/javascript`，347 KB | — |
| 6 | API 未被吞 | `curl .../api/health/db` / `/api/videos` | 均返回 JSON（修复路由顺序后复测通过） | — |
| 7 | 幂等防重入 | 再次 `run/start --port 8123 --no-browser` | `已在运行 PID=9983`，未重复拉起 | 0 |
| 8 | 降级模式 | `FRONTEND_DIST=/tmp/nonexistent-dist-xyz run/start --port 8124 --no-browser` | `WARN 未找到 .../index.html，将以「仅 API」模式启动`；根路径返回 `{"web_ui":false,...}`（`application/json`） | 0 |
| 9 | 双击入口（模拟） | `FRAMES_PORT=8126 bash "run/启动帧间.command" < /dev/null` | 完整流程跑通：检查 → 拉起 PID 10099 → 就绪 → `浏览器 已打开界面`；界面 200 | 0 |
| 10 | 停止 | `run/stop --port 8123` / `8124` / `8126` | 均 `停止完成  端口已释放`，PID/元信息文件已清理 | 0 |
| 11 | 停止后状态 | `run/status --port 8123` | `未在运行  端口 8123 空闲` | 1 |
| 12 | 不影响既有服务 | 全程检查 `backend/data/uvicorn.pid` 与 8000 | PID 文件仍为 `7558`，8000 服务持续 200，未被重启或覆盖 | — |

> 说明：第 9 项验证的是双击入口脚本本身（`.command` 的包装、端口解析、浏览器调用、退出码）。`webbrowser.open` 走 macOS 的 `osascript` 通道，返回成功即视为已打开系统默认浏览器。

## 6. Windows 侧待实机冒烟

对应 [12-windows-smoke-checklist.md](12-windows-smoke-checklist.md) 的 W19–W22：双击入口、端口占用提示、中文路径、`chcp` 编码。本机未验证，不作"已通过"结论。

## 7. 常见问题

| 现象 | 处理 |
|---|---|
| `[ERROR] 端口被占用` | 换端口 `--port 8081`，或双击「停止帧间」后重试；提示里给出的 PID 可到任务管理器核对 |
| `[WARN] 前端界面 未找到 .../index.html` | 界面未构建或未随包分发：在 `frontend/` 执行 `npm run build` 生成 `dist`；本次服务仍可用，但只有 API |
| 双击闪退 / 中文乱码（Windows） | 从 cmd 里执行 `python backend\scripts\launcher.py start` 看完整报错；入口已 `chcp 65001`，若仍乱码请反馈控制台类型 |
| 浏览器没自动打开 | 手动访问 `http://127.0.0.1:<端口>/`；或用 `--no-browser` 明确关闭该行为 |
| 想重启 | `run/restart`（或双击「停止帧间」后再双击「启动帧间」） |

*（内容由AI生成，仅供参考）*
*（内容由AI生成，仅供参考）*
