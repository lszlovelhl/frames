---
AIGC:
    Label: "1"
    ContentProducer: 001191440300708461136T1XGW3
    ProduceID: ce0d790a7b004233c1097246b343160b_8255604bac3811f1b128525400f8a581
    ReservedCode1: Pfrpuba9nSQiUq+l1UebTIXx06I9WbCMGVSM8XuDVxANJ4xTZ0RkUuiNcCxQo1fUOlJ4/NXwmdIWTuqJMyYa384JNvMyAfgE7zM9zq5Y+j0wzQ8tYH1ZariYk7KzgHBvft8sryVrUIUSzaknMjklGZP+ZBLTcvQZlpdCuwLwjp7BVZW+ObRWeQdo3EQ=
    ContentPropagator: 001191440300708461136T1XGW3
    PropagateID: ce0d790a7b004233c1097246b343160b_8255604bac3811f1b128525400f8a581
    ReservedCode2: Pfrpuba9nSQiUq+l1UebTIXx06I9WbCMGVSM8XuDVxANJ4xTZ0RkUuiNcCxQo1fUOlJ4/NXwmdIWTuqJMyYa384JNvMyAfgE7zM9zq5Y+j0wzQ8tYH1ZariYk7KzgHBvft8sryVrUIUSzaknMjklGZP+ZBLTcvQZlpdCuwLwjp7BVZW+ObRWeQdo3EQ=
---

# 11. 本地单体打包方案（决策 + 实施蓝图）

> 面向：单人开发者（梁龙科技 / 帧间）
> 目标：任意电脑（Win / Mac）免配置运行，本地优先、业务数据不出本机
> 状态：待拍板（本文档为决策与实施蓝图，开放问题见 §9）
> 版本：2026-09-09 · 编号说明：docs 现有 00–06，07–10 为空档，本方案取 `11-local-packaging.md`，防并行会话占用；若后续与其他文档撞号，先顺延并同步本文件头部说明。

## 0. 决策速览

已拍板方向共 4 项，全文围绕它们展开：

| # | 决策 | 一句话含义 |
|---|---|---|
| D1 | 数据库选 B：本地 SQLite | 本地业务库从 PostgreSQL 迁 SQLite（`frames.db`），不再依赖本机 PG 服务 |
| D2 | 本地单体 + 云端薄服务 | 全部业务数据在本地单体；云端只做账号 / 支付 / 点数 / 激活码 |
| D3 | 素材 A+B 保留策略 | 拆解完成自动清理大原片，本地只留轻量素材（m4a / preview / 帧图） |
| D4 | sidecar 随包分发 | ffmpeg / yt-dlp / faster-whisper 模型 / 可选 demucs 作为外部二进制随包带出 |

---

## 1. 背景与目标

### 1.1 现状

- 帧间为个人短视频拆解分析系统：后端 FastAPI + PostgreSQL（22 表、业务数据约 12MB），前端 React（Vite），素材采集 pipeline 下载原片到 `backend/data/media/url_xxx/`。
- 当前形态是 **dev 形态**：需要本机预装 Python 3.11、PostgreSQL、ffmpeg、yt-dlp，并手动跑 uvicorn + vite，无法迁移到任意电脑。
- 素材 A+B 已在代码中实现（`backend/app/media/cleanup.py` + `raw_files.media_policy`），打包方案需与其衔接，使单机素材目录体积可控。

### 1.2 目标（SMART 量化）

| 目标 | 量化口径 | 验收说明 |
|---|---|---|
| 免配置迁移 | 全新 Win / Mac 电脑，无需安装 PG / Python / ffmpeg / yt-dlp 即可启动 | 自检脚本全部 PASS 后可进入主界面 |
| 数据本地优先 | 业务数据 100% 存本地 `frames.db`，任何运行行为不上传业务数据 | 抓包 / 日志审计无业务数据外发（云薄服务仅账号/支付/点数/激活码） |
| 素材体积可控 | 拆解完成后单条素材体积从“数百 MB 原片级”降到“轻量素材级” | 保留物 = m4a 音轨（约 1MB/分钟）+ 预览片（按策略）+ 帧图 |
| 双平台可打包 | Win 与 Mac 各出一份可分发包 | 第 ⑤ 阶段双平台冒烟通过 |
| 可回滚 | PG 数据完整归档，新 SQLite 跑稳后再清 | §3.4 回滚策略生效 |

---

## 2. 总体架构

```
┌────────────────────────── 本地单体（任意电脑） ──────────────────────────┐
│                                                                          │
│  frames-app/                                                             │
│   ├─ frontend dist（静态资源，由后端同端口托管）                           │
│   ├─ backend  uvicorn :8000                                              │
│   │    ├─ frames.db（SQLite，业务数据全量在此：视频/拆解/创作/元素/账单…）│
│   │    └─ media/    url_xxx/{frames,manifest,audio_track.m4a,preview…}   │
│   ├─ sidecars/ ffmpeg | yt-dlp | whisper 模型 | demucs(可选)              │
│   └─ run/ 启动脚本 + selfcheck（自检）                                    │
│                                                                          │
│  分析产物、素材、历史数据 → 一律不出本机                                  │
└──────────────────────────────────────────────────────────────────────────┘
                              │  仅账号/支付/点数/激活码相关请求
                              ▼
┌──────────────────── 云端薄服务（运营侧 / 可选联网） ────────────────────┐
│  账号登录 / 订阅支付 / 点数账户 / 激活码校验（只接收这些业务字段）         │
└──────────────────────────────────────────────────────────────────────────┘
```

| 边界 | 本地单体 | 云端薄服务 |
|---|---|---|
| 数据 | 全部业务数据、素材、拆解/创作记录 | 无业务数据；仅账号与授权元信息 |
| 能力 | 视频解析、AI 拆解、分析、播放、素材清理 | 登录态、支付、点数扣减、激活码绑定 |
| 交互 | 单用户本机使用；断网仍可运行除云端能力外全部功能 | 需要时请求；失败不影响本地回放与已有分析 |
| Redis | 后端未直接引用，打包时剔除 | 不引入 |

> 数据流原则：**上行只发“需要云端做”的字段**（账号、支付、点数、激活码），永远不把 `raw_files` / 分析文本 / 素材路径打包上传。

---

## 3. 数据库 SQLite 化方案（D1）

### 3.1 影响面核实结论（已核实）

| 影响点 | 现状 | SQLite 方案 | 改动位置 |
|---|---|---|---|
| JSONB 类型 | 多处模型用 JSONB | 改为 SQLAlchemy 通用 JSON | `backend/app/models.py` |
| ARRAY(Text) | 若干文本数组字段 | 改为 JSON（业务无数组键级操作） | `backend/app/models.py` |
| PG 版 UUID | UUID 主键/外键 | 改用通用 Uuid（文本存储） | `backend/app/models.py` |
| DateTime(timezone=True) | PG timestamptz | 保留声明 + SQLite 存 UTC 文本；应用层统一 aware UTC | `backend/app/models.py`、写入兜底点 |
| `func.now()` | PG now() | SQLite 编译为 `CURRENT_TIMESTAMP`，可直接兼容 | 无代码改动 |
| `.ilike()` | `elements.py` / `products.py` 使用 | SQLite LIKE 兼容 ASCII 大小写不敏感；如需稳定跨库改为 `func.lower(col).like(...)` | 2 处文件（低危） |
| raw PG SQL / tsvector / JSONB 键级算子 | 业务代码未使用 | 无需处理 | — |

> 结论：**类型替换集中在 models.py，业务代码无 raw SQL 依赖**，SQLite 化成本主要在迁移与数据搬移，而非查询改写。

### 3.2 改动文件清单

| 文件 | 改动 |
|---|---|
| `backend/app/models.py` | JSONB→JSON、ARRAY→JSON、UUID→通用 Uuid、时间字段 UTC 约定注释 |
| `backend/app/services/elements.py` / `products.py` | 可选：`.ilike` 改为跨库等价写法 |
| `backend/app/core/config.py` | `DATABASE_URL` 默认 SQLite（如 `sqlite+aiosqlite:///frames.db`），支持环境变量覆盖（便于保留连 PG） |
| `backend/alembic/` | 新增 SQLite 兼容基线迁移 + 增量迁移；保留 PG 旧迁移不动 |
| `backend/requirements*.txt` | 去掉 `asyncpg` 直连依赖、去掉 Redis 相关包；新增 `aiosqlite` |
| 迁移工具目录 | 新增数据搬移脚本（导出 PG → 导入 SQLite） |
| `backend/app/main.py` 启动逻辑 | SQLite WAL PRAGMA、单进程约束、数据库文件目录解析 |

### 3.3 迁移与数据搬移步骤（严禁 drop_all/create_all 覆盖）

项目已有真实业务数据（约 12MB），必须走“归档 → 建新库 → 搬数 → 校验 → 回滚就绪”链路：

1. **PG 全量归档**：`pg_dump frames_dev -Fc -f backup/frames_pg_<date>.dump`，存到项目外或云备份，作为回滚底。
2. **生成 SQLite 基线**：新增 alembic 迁移，按 models.py 新类型建全部表（22 表）；不动现有 PG 库，不 drop_all。
3. **导出 PG 数据**：按表导出为 JSONL（每表一文件），字段按新类型对齐（UUID 字符串、JSON 直接序列化、时间统一转 UTC ISO）。
4. **导入 SQLite**：按外键依赖顺序批量插入，开启事务分批；处理 SQLite 外键约束（`PRAGMA foreign_keys=ON`）。
5. **数量与抽样校验**：逐表对比行数；抽查业务关键表（videos / analyses / creations / 账单）最近 10 条字段一致。
6. **应用冒烟**：切 `DATABASE_URL` 到 SQLite，跑自检 + 关键接口（详情/列表/播放/拆解回放）验证。
7. **PG 保留期**：原 PG 库归档后不删，跑稳 ≥ 1 个发布周期后再清（见 §9 开放问题 Q4）。

> 建议迁移脚本做成幂等 CLI：`python -m scripts.migrate_pg2sqlite --source-url <PG> --target <frames.db>`，输出每表行数与校验报告。

### 3.4 行为差异对照

| 维度 | PostgreSQL | SQLite | 影响与对策 |
|---|---|---|---|
| 并发写 | 多写者 | 单写者 + WAL | 本地单人单进程；启动 `PRAGMA journal_mode=WAL; synchronous=NORMAL; busy_timeout=5000`；uvicorn 单 worker |
| 时区 | timestamptz 感知 | 无原生 TZ | 全链路存 UTC；读取时按 UTC 解析；Pydantic 输出统一 `.isoformat()` |
| 大小写 / LIKE | 分 collation | ASCII 不敏感、中文按二进制 | 影响低；如需稳定改 `func.lower()` |
| UUID | PG 原生 | 文本存储 | 应用层主键不变，对外 ID 格式不变 |
| JSON | JSONB 键级操作 | JSON 文本 | 本业务无键级算子，序列化/反序列化由 SQLAlchemy 完成 |
| `func.now()` | 数据库时钟 | `CURRENT_TIMESTAMP` | 兼容；写入仍建议 `datetime.now(UTC)` 统一口径 |
| FTS / tsvector | 有 | 无（可挂 FTS5） | 当前未用，不引入 |

### 3.5 回滚策略

| 触发条件 | 动作 | 止损阈值 |
|---|---|---|
| 迁移后行数不符 / 关键表差异 > 0 | 停止切换，恢复 `DATABASE_URL` 指向 PG | 行数校验失败即回滚 |
| SQLite 版跑出数据错乱 | 切回 PG 连接；从归档 dump 可整库恢复 | 用户可操作时间内（≤ 1 天） |
| SQLite 版连续稳定运行 | 才允许清理 PG dump | 建议跑稳 ≥ 1 个发布周期 |

### 3.6 第①阶段执行记录与偏差（2026-09-09）

**实施结论**：①阶段完成，dev 默认已切 SQLite（`backend/data/frames.db`），21 张业务表行数逐一对比一致，关键接口 200。

**偏差 / 补充（已对齐 §3.1/§3.2 落地形态）**：
1. **UUID bind 需兼容 str**：旧代码路由以 `str` 直接入参查询（asyncpg 可隐式绑定），SQLAlchemy 通用 Uuid（SQLite CHAR 存储）bind 处理器只接受 `uuid.UUID`，直接绑定字符串会报 `'str' object has no attribute 'hex'`。`models.py` 增加 `Uuid(sqlalchemy.Uuid)` 子类：bind 层对 str 先 `uuid.UUID()` 归一。对现有 schema（CHAR(32) 存储）无影响，未改任何路由签名。
2. **alembic 采用「保留链新增」而非「新建基线」**：新迁移 `d1a0b2c3e4f5` 以旧 PG head（`c3d5e7f9a1b3`）为 down_revision，保留历史迁移链便于追溯；空 SQLite 先 `stamp c3d5e7f9a1b3` 再 `upgrade head`，旧 PG 迁移文件原样归档不删。
3. **迁移脚本**：`backend/scripts/migrate_pg2sqlite_20260909.py`（幂等 CLI：归档 pg_dump → JSONL 导出复用 → alembic 空基线 → 导入 → 行数+抽样校验；目标有数据默认拒绝，`--fresh` 才重建）。
4. **`.ilike()` 保留未改**：按 §3.1 判为低危（ASCII 大小写不敏感兼容 SQLite LIKE）；`elements.py`/`products.py` 维持原状。
5. **数据目录**：`backend/data/frames.db`（config 自动建目录）；PG 归档与 JSONL 备份放 `backend/data/backup/`（含时间戳）。
6. **PRAGMA**：`backend/app/db.py` 增加 sqlite 方言 connect 事件（WAL / synchronous=NORMAL / busy_timeout=5000 / foreign_keys=ON），§3.2 的 main.py 启动逻辑改为 db.py 统一注册。
7. **依赖**：requirements.txt 新增 `aiosqlite`；`asyncpg` 未移除（PG_LEGACY_URL 迁移/回滚仍需）。

---

## 4. 素材保留与目录规范（D3 衔接）

### 4.1 media 目录结构（随包路径约定）

```
<data_dir>/media/
└── url_<md5(url)前16位>/
    ├── frames/                  # 抽帧关键图（轻量，必保留）
    ├── manifest.json            # 采集清单（轻量，必保留）
    ├── audio_16k.wav            # 采集过程文件 → 清理时删除
    ├── video_truncated.mp4      # 超长截断预览（15min 级，保留角色）
    ├── audio_track.m4a          # auto 产物：128k AAC 音轨（保留）
    ├── preview.mp4              # keep_preview 产物：720p / ~2Mbps（保留）
    └── <完整原片>.mp4/.mov…     # 大原片 → 按策略删除
```

> 数据目录建议随 OS 用户目录走（Mac `~/Library/Application Support/frames`；Win `%LOCALAPPDATA%\frames`），与安装目录分离，便于升级不丢数据。完整原片、模型缓存不放入安装目录。

### 4.2 A+B 策略语义

| 策略 | 保留 | 删除 | 前端表现 |
|---|---|---|---|
| `auto`（默认） | m4a 音轨 + 帧图 + manifest；截断视频另留 video_truncated.mp4 | 完整原片、audio_16k.wav | `video_url` 为空 → 帧图 + 回源；截断仍可本地播放 |
| `keep_preview` | 再压 preview.mp4 作本地播放源 | 完整原片、audio_16k.wav | 本地播放 preview.mp4 |
| `keep_full` | 完整原片 | 不删 | 本地播放原片 |

前置条件：拆解 `done` 且转写/帧已落库，缺一不可；失败只记 warning，不阻塞。接口：`POST /api/videos/{id}/cleanup`（手动补清）、`POST /api/videos/{id}/media-policy`（改策略）。

### 4.3 单条素材体积预算（估算口径）

| 产物 | 估算公式 / 典型值 | 说明 |
|---|---|---|
| 完整原片 | 数百 MB ~ GB 级 | 清理前主要占用 |
| audio_track.m4a | 128kbps ≈ 0.96 MB/分钟 | 5 分钟 ≈ 5MB；10 分钟 ≈ 10MB |
| preview.mp4 | 2Mbps ≈ 15 MB/分钟 | 5 分钟 ≈ 75MB（策略性保留，默认 auto 不产生） |
| 截断版 video_truncated | 15 分钟截断（仅超长视频） | 按保留角色处理，避免误删 |
| 帧图 + manifest | KB 级 | 单素材轻量主体 |

> 结论：默认 auto 下，**一条短视频清理后本地体积从“数百 MB 原片”降到“数 MB 音轨 + 帧图”**；这是单机素材目录可长期可控的根基，与打包分发（用户数据目录在包外）无冲突。

---

## 5. 双平台目录布局与依赖清单（D4）

### 5.1 打包后目录布局（解压即跑形态，建议）

```
frames-win/ 或 frames-mac/
├── backend/
│   ├── app/                  # Python 业务代码（随运行时解释或冻结）
│   └── requirements.lock
├── frontend/dist/            # vite build 产物，后端 /static 托管
├── sidecars/
│   ├── ffmpeg/  (ffmpeg, ffprobe)
│   ├── yt-dlp/  (yt-dlp.exe / yt-dlp)
│   ├── whisper/ (faster-whisper 模型目录，可外置)
│   └── demucs/  (可选，模型可首次下载)
├── runtime/python/           # Python 运行时策略见 §5.3
├── run/                      # 启动脚本 + selfcheck
└── start.command / start.bat # 一键启动
```

数据目录（运行时生成，包外）：

```
<user_data_dir>/frames/
├── frames.db                 # SQLite 业务库
└── media/                    # §4.1 素材目录
```

### 5.2 sidecar 清单

| sidecar | 用途 | Win 获取 | Mac 获取 | 体积 / 说明 |
|---|---|---|---|---|
| ffmpeg + ffprobe | 抽帧/音轨/预览压制/合并 | 官方 Windows builds（ffmpeg.org / BtbN Releases 等） | Mac 静态编译包（evermeet.cx / ffmpeg.org 链接等） | 首次下载校验版本与完整性 |
| yt-dlp | 原片下载 | 官方 Windows exe | pip 包或独立可执行 | 需随版本更新 |
| faster-whisper 模型 | 转写 | 同左 | 同左 | 缓存约 179MB（`~/.cache/huggingface`）；后端 `transcribe.py` 支持 `WHISPER_MODEL_PATH`（本地 ctranslate2 目录，外置首选）/ `WHISPER_MODEL`（HF 模型名，默认 medium）覆盖，config 可注入为 `sidecars/models/` |
| demucs（可选） | BGM 分离 | 同左 | 同左 | 模型较大，默认不随包，首次按需下载 |

下载策略：主包只随 ffmpeg/yt-dlp 必需项；whisper 模型与 demucs 支持“首次启动按需下载 + SHA256 校验”，下载到用户数据目录而非安装目录，便于升级不重复下载。

### 5.3 部署形态

| 组件 | dev 形态 | 打包形态 |
|---|---|---|
| 后端 | `uvicorn app.main:app`（手动） | 启动脚本拉起 uvicorn，或冻结二进制后直接启动 |
| 前端 | `npm run dev`（Vite dev server） | `vite build` → dist 静态文件；由后端同端口托管，免 CORS 与双端口 |
| 数据库 | 本机 PG | SQLite `frames.db`（见 §3） |
| Python | 系统 Python + venv | 见 §6 打包方式 |
| 外部命令 | 系统 PATH 查找 | sidecar 目录优先注入 PATH / 配置绝对路径 |

### 5.4 Win / Mac 差异

| 维度 | Windows | macOS |
|---|---|---|
| 启动脚本 | `.bat` / `.cmd` | `.command` / shell |
| 二进制分发 | 官方 win exe | mac 静态包（x86_64 / arm64 需分别出包或标注） |
| 数据目录 | `%LOCALAPPDATA%\frames` | `~/Library/Application Support/frames` |
| 权限 | 一般无需提权 | 首次打开需右键/`xattr` 放行 Gatekeeper（如未签名） |
| Python 运行时 | 推荐 python-build-standalone 或 embeddable | 推荐 python-build-standalone（universal2 可选） |
| 验证 | 建议 Windows 实机/云 VM 冒烟 | 本机可验证 |

---

## 6. 打包方式对比与建议

### 6.1 方案对比

| 维度 | A. 脚本版（解压即跑） | B. PyInstaller 目录打包 | C. Nuitka 目录/单文件 |
|---|---|---|---|
| 维护成本（单人） | 低：脚本即文档 | 中：需维护 spec、每次升级重打 | 高：编译时间长、报错定位难 |
| 启动速度 | 快（解释执行，冷启动可接受） | 中 | 快（编译） |
| 体积 | 依赖解释器 + venv 包 | 冻结后去依赖，体积较小 | 类似 PyInstaller |
| 杀软误报 | 无/低（脚本+解释器） | Win 常见误报（无签名 exe） | 较少但仍可能 |
| 调试排障 | 直接看日志/源码 | 需剥离符号 | 最麻烦 |
| 适配变化（模型/ffmpeg 版本） | 改路径/换包即好 | 重打一次 | 重编译一次 |
| 单人 16GB 机器实感 | 轻 | 打包进程较重但可 | Nuitka 编译很吃 CPU/内存 |

### 6.2 推荐

**首期采用 A. 脚本版（解压即跑）**，理由：

1. 单人维护成本最低，脚本 = 可读可改的启动文档；
2. 用户目标是“免配置运行”，不等于“看不见源码”；脚本版已能满足；
3. sidecar 与数据目录天然在包外，升级 Python 依赖/ffmpeg 不需要重打整包；
4. PyInstaller/Nuitka 的杀软误报、构建复杂度对单人交付是净负担。

**B/C 留作后续选项**：当出现“面向非开发者分发、需要图标/无终端窗口/防篡改”等真实诉求时，再评估 PyInstaller（先）或 Nuitka（后），见 §9 开放问题 Q1。

---

## 7. 自检脚本骨架（selfcheck）

启动前执行 `run/selfcheck`，输出示例：

```text
frames selfcheck v0.1
[OK]   Python 运行时        python 3.11.x (runtime/python)
[OK]   依赖完整性           app + 依赖包 可 import（fastapi/sqlalchemy/aiosqlite/…）
[OK]   ffmpeg               v6.x  /  ffprobe v6.x (sidecars/ffmpeg)
[OK]   yt-dlp               yt-dlp 2026.x (sidecars/yt-dlp)
[OK]   whisper 模型         faster-whisper model 存在 (FRAMES_WHISPER_MODEL_DIR=<…>)
[WARN] demucs 模型          未安装（可选，将按需下载）
[OK]   数据库文件           <data_dir>/frames.db 可读写；WAL=on
[OK]   素材目录             <data_dir>/media/ 可写
[OK]   端口 8000            空闲可用
[INFO] 云薄服务连通性       离线模式可用（账号/支付功能将受限）

自检未通过项：
（无）
启动：run/start
```

检查清单与预期：

| # | 检查项 | 通过标准 | 失败处理 |
|---|---|---|---|
| 1 | Python 运行时 | 版本 ≥ 3.11 且为本包解释器 | 提示重装包 |
| 2 | 依赖 import | fastapi/sqlalchemy/aiosqlite 等可 import | 提示重装依赖或升级包 |
| 3 | ffmpeg/ffprobe | `ffmpeg -version` 成功 | 提示缺失并给出侧载/下载指引 |
| 4 | yt-dlp | `yt-dlp --version` 成功 | 同上 |
| 5 | whisper 模型目录 | 模型文件存在且可读 | 首次启动按需下载并校验 SHA256 |
| 6 | 数据库文件 | 文件可创建/打开、PRAGMA 校验、WAL 生效 | 给出目录写权限指引；不自动删库 |
| 7 | 端口 | 8000 未被占用 | 提示占用 PID，可选改端口 |

> 自检脚本退出码：全 PASS=0；有 WARN=1；有 ERROR=2，供启动脚本/CI 判断。

---

## 8. 实施顺序 ①–⑤

> 负责人默认均为本人（梁龙科技），每阶段独立可验证；标注预估为中性口径、供排期参考，不视为承诺。

### ① SQLite 化改造 + 迁移工具（先行验证，不切默认）

| 项 | 内容 |
|---|---|
| 任务 | models.py 类型替换；config 支持 SQLite URL；requirements 增删；alembic 新增兼容基线；编写 PG→SQLite 迁移 CLI（归档/导出/导入/校验） |
| 交付物 | 代码改动 + 迁移 CLI + 一份 SQLite 空库验证报告 |
| 验收 | 在副本库上完成“PG dump → 空 SQLite 基线 → 12MB 搬入 → 22 表行数一致”；应用连 SQLite 跑通关键接口；PG 库原样归档 |
| 风险 | 类型替换遗漏 → 用 22 表行数与接口冒烟兜底；迁移脚本 bug → 幂等 + 回滚 PG 兜底 |
| 工作量预估 | 3–5 天 |

### ② 打包目录规范 + 双平台启动骨架

| 项 | 内容 |
|---|---|
| 任务 | 确定包目录/数据目录规范；sidecar 下载器与校验；start 脚本（uvicorn + 托管 dist）；构建前 `vite build` 流程 |
| 交付物 | `run/` 骨架 + sidecar 清单 manifest + 下载校验脚本 |
| 验收 | Mac 本机：清理环境变量后仅靠包目录即可 `start` 拉起并打开页面；whisper 模型路径可配置 |
| 风险 | 依赖路径硬编码 → 统一用相对包目录解析 + 用户数据目录解析 |
| 工作量预估 | 2–4 天 |
| 进展标注（2026-09-10） | Mac 侧已落地「类 app」启动体验：`backend/app/main.py` 单端口托管 `frontend/dist`（缺失时降级为仅 API）+ `backend/scripts/launcher.py`（纯标准库 start/stop/status/restart）+ `run/start`·`run/stop`·`run/status` + 双平台双击入口（macOS `.command` / Windows `.bat`），Mac 本机实测通过；详见 [13-app-launcher.md](13-app-launcher.md)，Windows 侧待冒烟项 W19–W22 |

### ③ 素材 A+B 与存储策略回归

| 项 | 内容 |
|---|---|
| 任务 | 确认 cleanup.py 在 SQLite 环境正常；auto/keep_preview/keep_full 分别跑通；无原片降级播放器回源验证 |
| 交付物 | 三策略冒烟记录 + media 目录体积前后对比 |
| 验收 | 拆解完成自动清理触发；video_url 为空时前端帧图+回源可点；keep_preview 本地播放正常 |
| 风险 | SQLite 迁移后 raw_files JSON 读写路径变化 → 回归素材类接口 |
| 工作量预估 | 1–2 天 |

### ④ 自检脚本 + Windows 打包冒烟

| 项 | 内容 |
|---|---|
| 任务 | 完整 selfcheck 输出（§7）；打 Windows 包；准备 Win 验证（本机或 VM/云）；Mac 包同步整理 |
| 交付物 | 双平台 zip/tar 包 + 自检 PASS 截图 |
| 验收 | 两台机器（Win 实机或 VM + Mac）免配置启动；核心路径：建档→下载→拆解→清理→回放 |
| 风险 | Windows 未实机自测（见开放问题 Q5）；Gatekeeper 放行指引缺失 |
| 工作量预估 | 3–5 天（含 Win 环境准备） |
| 进展标注（2026-09-10） | Mac 侧已落地：`backend/scripts/selfcheck.py`（11 项检查 / `--json` / 退出码 0-1-2，本机实测通过）+ `run/selfcheck`（macOS 已验证）/ `run/selfcheck.bat`（待 Win 冒烟）；`prepare_sidecars.sh` 预检已全绿；同日补齐「类 app」启停入口（见 [13-app-launcher.md](13-app-launcher.md)）。Windows 待测项与待补产物见 [12-windows-smoke-checklist.md](12-windows-smoke-checklist.md) |

### ⑤ 云端薄服务剥离与联调

| 项 | 内容 |
|---|---|
| 任务 | 账号/支付/点数/激活码模块抽成云薄服务接口；本地离线降级策略；激活码绑定与防呆 |
| 交付物 | 云薄服务接口文档 + 本地离线模式开关 + 联调记录 |
| 验收 | 本地业务数据不出包（抓包审计）；断网时本地功能可用、云端功能提示明确 |
| 风险 | 支付/点数改动引入计费回归 → 用测试账号联调 + 已有账单脚本回归 |
| 工作量预估 | 4–6 天 |

---

## 9. 仍需拍板的开放问题清单

以下仅列选项与权衡，不替决策。

| # | 问题 | 选项 | 权衡 | 影响 | 建议拍板时机 |
|---|---|---|---|---|---|
| Q1 | 首期做脚本版还是直接 PyInstaller/Nuitka？ | 脚本版优先（推荐依据 §6） / 直接目录打包 | 脚本版开发快、好维护；冻结版分发观感好但构建与误报成本高 | 决定 §8 ②④ 交付形态 | ② 开工前 |
| Q2 | whisper 模型是否随包？ | 随包（体积 +179MB+） / 首次下载（默认） / 用户手动外置 | 随包离线最稳但包大；首次下载体验取决于网络；外置最灵活 | 影响包体积与首启体验 | ② 开工前 |
| Q3 | demucs 是否进入 v1 包？ | 不随包、按需下载（建议） / v1 就带 | 体积与首次下载 vs 功能完整 | 影响 §5.2 依赖清单 | ① 完成后 |
| Q4 | 原 PG 库与归档 dump 保留多久清理？ | 保留 1 个发布周期 / 保留 N 天（自定） / 长期不删 | 保留越久越稳但占空间；清理过早回滚困难 | 影响 §3.3 归档期 | SQLite 切默认后 |
| Q5 | Windows 自测谁做、用什么环境？ | 本机 Win 实机（若有） / 云 VM / CI 跑一次 / 先发内部测试 | 云 VM 成本可控但 GPU/ffmpeg 性能不同；实机最真实但取决于是否有设备 | 影响 ④ 验收真实性 | ④ 开始前 |
| Q6 | 云薄服务本期范围？ | 仅激活码+账号 / 含支付与点数 / 先纯离线本地授权 | 范围越大越接近商业化；纯离线实现最快但无法远程运营 | 决定 ⑤ 工作量 | ⑤ 开始前 |
| Q7 | 包是否带自动更新器？ | 不带（手动换包） / 简单版本检查提示 / 全自动更新 | 全自动最顺滑但引入签名与回滚复杂度；单人建议先手动 | 影响长期维护 | ⑤ 后 |

---

## 附录：关联文档

| 文档 | 关系 |
|---|---|
| `docs/02-technical-architecture.md` | 既有架构，本方案为打包/存储形态的落地分支 |
| `docs/04-data-model.md` | 数据模型总览，SQLite 迁移以它为准核对 22 表 |
| `docs/05-points-billing.md` / `06-payment-integration.md` | 云端薄服务涉及的计费/支付上下文 |
| `backend/app/media/cleanup.py`、`backend/app/routers/videos.py` | A+B 素材策略现状实现 |
*（内容由AI生成，仅供参考）*
