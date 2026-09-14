---
AIGC:
    Label: "1"
    ContentProducer: 001191440300708461136T1XGW3
    ProduceID: ce0d790a7b004233c1097246b343160b_a9f78364ad0511f18874525400287e28
    ReservedCode1: dx5hc+TOb1nVANrCMrNsI7Sakbr+XTq8pujaxrhYOgUkbbclWrdXWtln8XMdEOiFfF/1ntBWblJo1+BZgc7S31m79dTlVjMU8ua8coi2cs2X9QHbL5Sn0O93rwlpOGmkBLocmi4nBL0tMA6fkZt/HljvQcNPbRuR0TcmutsvemR0NiuAQg97kgsCXWA=
    ContentPropagator: 001191440300708461136T1XGW3
    PropagateID: ce0d790a7b004233c1097246b343160b_a9f78364ad0511f18874525400287e28
    ReservedCode2: dx5hc+TOb1nVANrCMrNsI7Sakbr+XTq8pujaxrhYOgUkbbclWrdXWtln8XMdEOiFfF/1ntBWblJo1+BZgc7S31m79dTlVjMU8ua8coi2cs2X9QHbL5Sn0O93rwlpOGmkBLocmi4nBL0tMA6fkZt/HljvQcNPbRuR0TcmutsvemR0NiuAQg97kgsCXWA=
---



# 帧间 Frames

> 梁龙科技 · 爆款拆解驱动的 AI 创作工作台
> 前身：viral_analyzer（v1 代码遗失，本项目为 v2 重写）

## 项目定位

用拆解数据 + AI，把编导的隐性经验翻译成普通创作者可调用的模板与建议。核心不是剪辑工具，而是"帮用户想清楚拍什么、怎么拍"的创作工作台。

## 文档导航

| 文档 | 内容 | 维护时机 |
|------|------|----------|
| [01-product-design.md](01-product-design.md) | 产品设计：定位、用户、数据飞轮、功能模块、形态决策 | 产品迭代时 |
| [02-technical-architecture.md](02-technical-architecture.md) | 技术架构：选型、目录、启动、配置、网关设计 | 架构变更时 |
| [03-development-log.md](03-development-log.md) | 开发日志与断点续传手册 | **每次开发后必更** |
| [04-data-model.md](04-data-model.md) | 数据库模型设计 | 建表前 |
| [05-points-billing.md](05-points-billing.md) | 点数计费与防亏损设计（含免计费模式开关） | 计费规则调整时 |
| [06-payment-integration.md](06-payment-integration.md) | 真实支付接入方案（草案） | 支付渠道对接时 |
| [11-local-packaging.md](11-local-packaging.md) | 本地单体打包方案（SQLite + 脚本骨架） | 打包/发布调整时 |
| [12-windows-smoke-checklist.md](12-windows-smoke-checklist.md) | Windows 冒烟待测清单（阶段④配套，W1–W22） | Windows 实机/VM 验证时 |
| [13-app-launcher.md](13-app-launcher.md) | 类 app 启动体验：单端口托管 + 跨平台启动器 + 双击入口 | 启动方式/入口脚本调整时 |

> 数据库现状：默认已切换为本地 SQLite（`backend/data/frames.db`，PG 仅旧库迁移用），素材采用 A+B 清理策略（拆解后清理大原片）。2026-09-09 的大改动清单见 [03-development-log.md](03-development-log.md) 当日小节。

> 拆解链路现状（2026-09-10 阶段⑤）：支持批量提交（最多 20 条/次）与进度常驻（`/api/analyses/active`，3s 轮询）；模型档位统一走 `runtime_config`，**默认免费档（`free_flash → flash`）**，创作台与五层拆解一致；文案主链路为本地 ASR（`faster-whisper-small` 离线）并强制简体，平台字幕/OCR 仅兜底；短视频（< 5 分钟）走密集关键帧档（`FRAMES_POLICY_VERSION = 2`），素材 manifest 记录 `media_spec`，抽帧策略或 ASR 管线升级后旧链接会**自动重采集一次**。细节与验收数据见 [03-development-log.md](03-development-log.md) 2026-09-10 阶段⑤小节。

> 计费现状（2026-09-10 阶段⑥）：**默认免计费模式**（`billing_enforced = false`）。创作台对话/创作指南、拆解、元素与产品 AI 等**不再因点数不足被 402 拦截**，照常记录 AI 用量账与点数流水（类型 `free_usage`，**不扣余额**、不计入「今日已用」与日上限）。充值渠道上线后，把 `backend/data/runtime_config.json` 的 `billing_enforced` 改为 `true`（改完即生效、无需重启），或在启动时注入环境变量 `FRAMES_BILLING_ENFORCED=1`（优先级更高），即恢复强制计费，业务代码零改动。规则见 [05-points-billing.md](05-points-billing.md) §4.1，验收数据见 [03-development-log.md](03-development-log.md) 阶段⑥小节。

> 断点续传第一入口：先读 [03-development-log.md](03-development-log.md) 的最新记录，确认当前进度与下一步。

## 快速启动

> 当前默认 SQLite 本地库（`DATABASE_URL=sqlite+aiosqlite` → `backend/data/frames.db`）。日常使用**像用 app 一样启动**：单端口 `http://127.0.0.1:8000/` 即完整界面，无需另起前端；详见 [13-app-launcher.md](13-app-launcher.md)。

**方式一：双击（日常使用）**

| 平台 | 启动 | 停止 |
|---|---|---|
| macOS | 双击 `run/启动帧间.command` | 双击 `run/停止帧间.command` |
| Windows | 双击 `run/启动帧间.bat` | 双击 `run/停止帧间.bat` |

启动窗口可直接关闭，服务在后台运行；启动成功会自动打开系统默认浏览器。

**方式二：终端（等价命令）**

```bash
run/start          # 启动（默认 8000，自动开浏览器）
run/status         # 查看状态
run/stop           # 停止
run/start --port 8080 --no-browser   # 其他端口 / 不开浏览器
```

前置（首次）：`cd backend && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`；若界面是旧版，在 `frontend/` 执行 `npm run build` 生成 `frontend/dist`。

**方式三：开发模式（前后端分离，改代码用）**

前置：macOS 已装 Homebrew（`/opt/homebrew`），Node ≥ 20；默认走本地 SQLite（无需 PostgreSQL）。

```bash
# 后端
cd ~/Projects/frames/backend
.venv/bin/uvicorn app.main:app --port 8000 --reload

# 前端（另开终端）
cd ~/Projects/frames/frontend
npm install       # 首次
npm run dev       # http://localhost:5173，热更新
```

> 旧入口 `bash backend/scripts/run_app.sh` 仍可用（与 `run/start` 共用同一 PID 文件，可互相启停）。

## 状态徽标

- 阶段：骨架搭建 ✅ → 数据模型设计 ✅ → 拆解链路（批量提交 + 进度常驻 + 免费档统一 + 短视频密帧 + 简体 ASR）✅ → 素材版本自愈与 ASR 质量修复 ✅ → 可切换免计费模式（默认放开点数校验）✅
- 待办：Windows 实机冒烟（[12-windows-smoke-checklist.md](12-windows-smoke-checklist.md)）、E 域元素变异/组合飞轮、充值渠道上线后切回强制计费（`billing_enforced: true`）
- 详细进度见 `03-development-log.md`
*（内容由AI生成，仅供参考）*
*（内容由AI生成，仅供参考）*
