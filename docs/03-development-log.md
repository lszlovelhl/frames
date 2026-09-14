---
AIGC:
    Label: "1"
    ContentProducer: 001191440300708461136T1XGW3
    ProduceID: ce0d790a7b004233c1097246b343160b_a88a0cc9ad0511f1b128525400f8a581
    ReservedCode1: OaeckfQuRaKnmIYZHGZZV4xflrDxQ20cmCQNL8JJ4SsqC3YGV95/sNRL6aW+B89BnDQK4xZNRe3nXnBCqwQiD2TZjuVNP8iRkadURYkUiWz+vxAeE5ZRRSDwKQ+19DPi4ZdK+KOVZermS//zlhj0gfJsKVeFTE6bmwkNdcX5Mw784cmTXVhraWoxYB8=
    ContentPropagator: 001191440300708461136T1XGW3
    PropagateID: ce0d790a7b004233c1097246b343160b_a88a0cc9ad0511f1b128525400f8a581
    ReservedCode2: OaeckfQuRaKnmIYZHGZZV4xflrDxQ20cmCQNL8JJ4SsqC3YGV95/sNRL6aW+B89BnDQK4xZNRe3nXnBCqwQiD2TZjuVNP8iRkadURYkUiWz+vxAeE5ZRRSDwKQ+19DPi4ZdK+KOVZermS//zlhj0gfJsKVeFTE6bmwkNdcX5Mw784cmTXVhraWoxYB8=
---



# 帧间 · 开发日志与断点续传手册

> 规则：每次开发结束，在此追加当日记录。下次开工先读本文件最新一节。

## 2026-09-04（首日）

### 本轮完成

1. **环境体检与修复**
   - 发现系统级 Homebrew 6.0.11 已装 Node v25.9.0 / PostgreSQL 16.13（运行中）/ Python 3.14.3 / ffmpeg / redis
   - 修复 `~/.zshrc`：追加 `export PATH="/opt/homebrew/bin:$PATH"`（此前终端找不到 brew/node/psql）
   - 清理误装的家目录 Homebrew 副本

2. **项目初始化**
   - 创建 `~/Projects/frames`，Git 仓库已 init（分支 main）
   - Git 身份：lszlovelhl / 52390663+lszlovelhl@users.noreply.github.com（全局已有）

3. **骨架搭建与连通验证**
   - 后端：`backend/.venv` + FastAPI 0.141.1 + SQLAlchemy 2.0.52 + asyncpg 0.31.0
   - 入口 `backend/app/main.py`：根路径 + `/api/health/db` 数据库健康检查
   - 数据库：`frames_dev` 已建（PostgreSQL 16，连接用户 zhuolittlelong，本机免密）
   - 验证通过：`curl http://127.0.0.1:8000/` → ok；`/api/health/db` → database ok
   - 前端：`frontend/` Vite react-ts 脚手架，依赖已装

4. **文档体系**
   - docs/00-README.md、01-product-design.md、02-technical-architecture.md、03-development-log.md（本文档）、04-data-model.md（下一步建）

### 本轮完成（第二阶段 · 同日）

1. **远程仓库（私密）**
   - GitHub 私密仓库 `frames` 已创建并推送：https://github.com/lszlovelhl/frames（仅 lszlovelhl 账号可见）
   - 仓库含全部代码与 docs，main 分支与本地同步

2. **DeepSeek 模型接入（帧间专用 Key）**
   - Key 写入 `backend/.env`（已被 .gitignore 排除，不入库）
   - 实测 `deepseek-v4-flash` 调用通过；别名映射 flash / pro / vision
   - 说明：Key 在聊天记录出现过，用户选择暂不重置（自行评估风险）

3. **数据模型 v0.3 落盘**
   - `docs/04-data-model.md`：6 域（账户配置/素材建档/拆解/元素变异/创作/回流）16 表设计

4. **后端地基升级（v0.2.0）**
   - `app/core/config.py`：dotenv 读取（DATABASE_URL + DEEPSEEK_*）
   - `app/db.py`：async engine + session + Base
   - `app/models.py`：16 张 ORM 表（对齐 04 文档）
   - `app/ai.py` + `app/routers/ai.py`：DeepSeek 网关 `/api/ai/chat`、`/api/ai/models`
   - Alembic async 迁移：初始版本 `7df68a88`，16 表已建到 frames_dev
   - 验证通过：health/db ok；ai/chat 真实返回（flash 思考+回复）

5. **前端 Tailwind v4**
   - `vite.config.ts` 接入 `@tailwindcss/vite`；`src/index.css` 换为 `@import "tailwindcss"`；清空模板 App.css
   - `npm run build` 通过

6. **踩坑备忘**
   - shell_executor 非交互 shell 无 npm/node PATH：命令前 `export PATH="/opt/homebrew/bin:$PATH"`
   - 系统 pip3 受 PEP 668 保护：一律用 `backend/.venv/bin/pip`

### 断点重启步骤（任何时候回来从这里开始）

```bash
# 0. 后端依赖装好后若模型有改动，先跑迁移
cd ~/Projects/frames/backend && .venv/bin/alembic upgrade head

# 1. 启动 PostgreSQL（若未运行）
brew services start postgresql@16

# 2. 启动后端
cd ~/Projects/frames/backend && .venv/bin/uvicorn app.main:app --port 8000

# 3. 启动前端（另开终端）
cd ~/Projects/frames/frontend && npm run dev

# 4. 验证
curl http://127.0.0.1:8000/api/health/db
curl http://127.0.0.1:8000/api/ai/models
curl -X POST http://127.0.0.1:8000/api/ai/chat -H 'Content-Type: application/json' \
  -d '{"messages":[{"role":"user","content":"你好"}],"model":"flash"}'
# 浏览器打开 http://localhost:5173
```

### 本轮完成（第三阶段 · 同日 · 后端拆解链路 + 前端基础布局）

1. **后端五层拆解服务（v0.3.0 主线闭环）**
   - `app/services/analysis.py`：`create_video_record`（建档，字幕落 raw_files）+ `run_analysis`（L1-L5 逐层调 AI 网关，建档/落库/结果组装）+ `analysis_result`
   - 健壮性：空内容重试与升温（max_tokens 上限遇 reasoning 吃满时空返回时放大重试）；L4/L5 输出约束收敛到 v2
   - `app/routers/videos.py`：`POST /api/videos`、`GET /api/videos`、`GET /api/videos/{id}`、`POST /api/videos/{id}/analyse`、`GET /api/analyses/{id}`
   - `app/main.py`：加 CORSMiddleware 允许 http://localhost:5173

2. **提示词种子脚本**
   - `backend/scripts/seed_catalog.py`：5 层拆解提示词 v1（对齐方法论 A1-A5）+ 品类模板 + 演示编导账号；幂等，旧版写 archived、新版 active

3. **冒烟验证**
   - demo001（"程序员 30 天做出 AI 产品"复盘视频）建档并触发五层拆解：L1/L2 通过，L3-L5 因 max_tokens 被 reasoning 吃光返回空 → 调大 8192 + 升温重试 → 通过；L4/L5 收缩输出约束为 v2 后重跑
   - 终态：`status=done`，L3 segments=7、L4 notes=10、L5 elements=6

4. **前端基础布局（暗色「帧间」工作台）**
   - `src/api.ts`：与后端全 API 契约对齐的 fetch 封装（类型化）
   - `src/App.tsx`：左侧导航（拆解工作台/拆解库/创作台/系统设置）+ 主区路由状态
   - 视图：`BreakdownView`（建档表单 + 字幕粘贴 + 五层拆解一键执行）、`LibraryView`（素材列表 + 拆解详情）、`CreateView`（创作台占位，标注下阶段）、`SettingsView`（服务健康 + 模型网关别名表）
   - `components/ResultPanels.tsx`：SummaryPanel / SegmentsPanel / NotesPanel / ElementsPanel / LayersRail（五层进度圆点）
   - `npm run build` 通过（tsc + vite）

5. **联通性验证**
   - 前端 dev http://localhost:5173 → 200；后端 health/db ok
   - API 契约逐字段核对：`GET /api/videos` 返回 latest_analysis{id,status}；`GET /api/analyses/{id}` summary 含 one_liner/topic/hook_hypothesis/target_audience，L3 segments=7、L4 notes=10、L5 elements=6 —— 与前端解析完全匹配
   - 踩坑：browser-agent 无法启动（Marvis browser-automation socket 未运行），UI 浏览器级目验未执行，待人工打开 http://localhost:5173 复核

### 下一步（待办）

- [x] **推送远程仓库（私密）**：https://github.com/lszlovelhl/frames
- [x] 核心数据模型（docs/04-data-model.md v0.3，16 表已建）
- [x] DeepSeek API Key 配置 + 模型网关 `/api/ai/chat`
- [x] 前端 Tailwind v4
- [x] 前端基础布局：侧边栏 + 主区（拆解 / 创作 / 元素库 / 设置四视图）
- [x] 提示词模板种子数据（prompt_templates 首批五层模板，对齐方法论 A1-A5）
- [x] 拆解主流程 API：视频建档 → 五层拆解编排（任务化调 AI 网关）
- [ ] UI 浏览器级目验：人工打开 http://localhost:5173 过一遍四视图与拆解详情
- [x] 元素库：查询 / 采纳 / 纠错 API 与页面（质量自循环）
- [x] 创作台：对话式驱动 + 产物卡片化（落卡 Creation/CreationAsset）→ 跑通 MVP「拆解 → 创作」链路
- [ ] 回填蓝图 v0.1 待补全项（v1 表结构、提示词资产细节、部署方式）

### 本轮完成（第四阶段 · 元素库跨片聚合 + 创作台落地）

1. **元素库（跨拆解聚合 + 质量自循环）**
   - 后端 `GET /api/elements`：跨 analysis 聚合，状态/分类/关键词过滤，draft 优先；返回 {total, status_counts, items}，item 含 video 来源
   - 前端新增「元素库」视图：状态 tab、分类筛选、搜索、元素卡（采纳/驳回/纠错弹窗）、跳转拆解库对应素材
   - 现状：22 个元素、3 个 analysis，均为 draft 待质控

2. **创作台（对话式驱动 + 产物卡片化，用户确认形态）**
   - 后端 `app/routers/creations.py`：`POST /api/creations/chat`（注入元素上下文生成，返回 {reply, used_elements, model}）、`POST /api/creations` 落卡、`GET/DELETE /api/creations(/id)` 产物库；`main.py` 注册
   - 前端重写 `CreateView.tsx`：消息流 + 引用元素弹窗 + 落卡 + 产物库抽屉；`api.ts` 增 creations* 方法
   - E2E 验证通过：对话生成 → 落卡 → 产物库查看；修复产物时间显示差 8 小时（UTC 本地化）
   - 落卡 role_view=编导 写入 script 资产；产物库有冒烟测试残留「程序员副业钩子测试」可删

### 关键路径备忘

- 项目根：`~/Projects/frames`
- 蓝图文档（会话产出）：`~/Library/.../workspace/conv_*/output/梁龙科技-帧间重建蓝图-v0.1.md`（核心方法论已并入 docs/01）
- 连接串默认：`postgresql+asyncpg://zhuolittlelong@localhost:5432/frames_dev`
*（内容由AI生成，仅供参考）*

### 本轮完成（第五阶段 · 产物版本迭代，收口创作闭环）

1. **后端 `app/routers/creations.py`**
   - 新增 `ContinueChatReq` / `VersionSaveReq` 请求模型
   - `POST /api/creations/{id}/chat`：带历史继续对话，自动注入该产物**最新 script 资产**作为改稿基线，返回 {reply, model, current_version, base_asset_id, used_elements}
   - `POST /api/creations/{id}/versions`：将满意回复**存为新版本资产**（CreationAsset.parent_id 指向上一版形成版本链），parent_id 缺省自动取当前最新资产
   - `GET /api/creations/{id}` 资产列表改用 `_asset_view`：按 created_at 升序返回并带 version 字段（v1..vN）；`_load_creation`/`_asset_view`/`_group_versions` 辅助函数
   - 冒烟：v1 → versions 存 v2（parent_id 链正确）→ 详情返回 v1/v2 两段

2. **前端 `CreateView.tsx` / `api.ts`**
   - `CreationAssetView` 增 `version`/`parent_id`；api.ts 增 `creationsContinueChat`、`saveCreationVersion`
   - 产物库抽屉：条目右侧「继续修改」入口、详情资产块显示 **v 版本徽标**与落卡时间
   - 新增**改稿模式**（editing state）：进入后顶部琥珀色条「正在改稿《标题》· vN」+ 退出按钮；输入区平台/意图下拉隐藏；对话走 `/chat` 续聊接口并注入最新版本
   - 回复卡按钮在改稿模式下切换为「存为新版本」（`versions` 接口），notice「已存为 vN」，改稿条版本号同步递增；空内容保护防止正文未完成即保存
   - `npx tsc --noEmit` 通过

3. **E2E 浏览器目验（Playwright + Chrome headless，6 步全通过）**
   - 产物库列表含「继续修改」；详情 script v1/v2 徽标正确；点「继续修改」进入改稿模式（琥珀条 v2、下拉消失）
   - 发送「把开头改得更炸一点」→ 完整新版文案 → 存为新版本 → notice「已存为 v3」→ 产物库详情 v1/v2/v3 三段齐全，无控制台报错
   - 截图目录：`~/Projects/frames/tmp_e2e/`

### 下一阶段建议

- [x] 创作数据回流：元素被引用次数 usage（CreationAsset.used_elements 或独立引用表）反哺元素库热度排序
- [x] 产物库冒烟残留「程序员副业钩子测试」清理
- [ ] 改稿模式支持选择「基于某个历史版本」续改（当前固定基于最新版）

### 本轮完成（第六阶段 · 创作数据回流：元素引用热度反哺元素库）

1. **后端 `app/routers/elements.py`**
   - `GET /api/elements` 增加**创作数据回流聚合**：扫描全部 `Creations.core_elements`（JSONB），统计每个 element_id 被多少个创作项目引用（同项目内去重），item 新增 `usage_count` 字段
   - 数据语义：按项目粒度计数（引用过该元素的产物数），版本迭代沿用元素不重复累计；删除产物后级联解除引用，usage 自动归零

2. **前端 `ElementsView.tsx` / `api.ts`**
   - `ElementItem` 增 `usage_count`
   - 元素卡新增紫色徽标「被 N 个创作引用」（usage>0 才显示，title 提示语义）
   - 过滤行新增「排序方式」下拉：**质控优先**（默认，保持 draft 优先质控序）/ **热度优先**（usage_count 降序，热度相同维持原序）

3. **E2E 浏览器目验（Playwright + Chrome headless，4 步全通过）**
   - 受控造数：给冒烟产物「程序员副业钩子测试」写入对元素「时间戳复盘法」的引用 → 元素库出现紫色徽标「被 1 个创作引用」
   - 切「热度优先」：带徽标元素升至列表第一位
   - 创作台产物库删除两条冒烟残留（「程序员副业钩子测试」含 v1/v2/v3、另一条含主推钩子方案），空态文案正确
   - 返回元素库刷新：徽标消失、usage 归 0 —— **删除产物 → 解除引用 → 热度回流**闭环验证通过
   - 截图目录：`~/Projects/frames/tmp_e2e/usage_refund/`

### 下一阶段建议

- [x] 改稿模式支持选择「基于某个历史版本」续改（当前固定基于最新版；后端 `parent_asset_id` 已就绪，需前端在产物详情版本块加「以此版续改」入口并透传）
- [ ] 元素库验收后做「元素变异/组合」入口（E 域飞轮再前进一步，编导需要的是拿来就改的组合方式而非原始元素）
- [ ] AI 网关 usage/计费落账可视化（Settings 面板可看每轮成本）
### 本轮完成（第七阶段 · 改稿模式支持选历史版本续改，版本树分叉）

1. **后端 `app/routers/creations.py`**
   - `ContinueChatReq` 增加 `base_asset_id: str | None = None`：可显式指定某个历史 script_asset 作为续聊基线
   - `POST /api/creations/{id}/chat`：传 `base_asset_id` 时校验并选中对应历史版本，否则仍取最新版；`version` 按基线在版本列表中的索引 +1 计算；系统提示语由「当前最新版全文」改为「当前基线版全文」；返回 `base_asset_id` 回显修正为请求值
   - 存版链路无需改动（`VersionSaveReq.parent_asset_id` 第五阶段已就绪），前端透传即可形成 v1→v2→v3 主链 + v2→v4 分支链

2. **前端 `CreateView.tsx` / `api.ts`**
   - `api.ts`：`creationsContinueChat` 增可选 `baseAssetId` 参数并透传 `base_asset_id`
   - `CreateView` editing state 增加 `baseAssetId` 字段（null=基于最新版）
   - 产物详情资产块：每个有正文的版本旁新增「以此版续改」按钮 → 以该版本为基线进入改稿模式（琥珀条显示 vN、notice 注明基线版本），继续对话/存版均透传该基线
   - `saveMessage` 存版时传 `parent_asset_id=editing.baseAssetId`，存后基线更新为刚存的新版本资产
   - `npx tsc --noEmit` 通过

3. **E2E API 级验证通过（脚本 `temp/e2e_version_branch*.py`）**
   - 造数：产物 v1 → v2（橙子标记）→ v3（香蕉标记）
   - 以 v2 为 `base_asset_id` 续聊 → 返回 `current_version=2`、`base_asset_id` 回显一致，AI 回复开头为「版本2：橙子标记——节奏」，**确认注入的是 v2 而非最新 v3**
   - 基于 v2 存 v4 → `parent_id` 指向 v2，详情资产链 v1→v2→v3 / v2→v4 分叉正确
   - 冒烟产物已清理；uvicorn 无 `--reload`，改动后需重启后端进程加载

4. **提交**：`ba958fb`（仅含本次 3 个文件；注意本仓库历史遗留大量未跟踪文件，勿用 `git add -A`）

### 下一阶段建议

- [ ] 元素库验收后做「元素变异/组合」入口（E 域飞轮再前进一步，编导需要的是拿来就改的组合方式而非原始元素）
- [ ] AI 网关 usage/计费落账可视化（Settings 面板可看每轮成本）
- [ ] 产物详情资产块弱化「版本顺序=优劣」暗示：分支场景可加「衍生自 vN」小标签（parent_id 已可支撑）

## 2026-09-09（SQLite 本地化 + A+B 素材策略 + 打包准备）

> 大改动日：数据库从 PG 切换到本地 SQLite（frames.db），素材存储引入 A+B 清理策略，并落定「本地单体打包」方案与脚本目录骨架。README 无版本日志结构，本次变更全部记于此节；README 相关小节已加指引。

### 本轮完成（A+B 素材清理策略）

1. **后端清理服务 `app/media/cleanup.py`**
   - `videos.raw_files.media_policy ∈ auto / keep_preview / keep_full`，缺省 **auto**；另存 `video_path / preview_path / audio_track_path / cleaned / cleaned_at / retained_files` 等字段
   - **auto**：拆解 done 后自动删除大原片与 `audio_16k.wav`，保留 `audio_track.m4a`（128k aac）作为音轨源；`video_path` 置空
   - **keep_preview**：先压 `preview.mp4`（720p/~2Mbps）再删原片；超长截断场景（auto+truncated）保留 `video_truncated.mp4` 并把 `video_path` 改指向它，作为本地播放源
   - 删除集合 = work_dir 顶层非保留角色视频 + 当前 video_path（若其名不在保留角色集合）；audio 音源优先取候选中体积最大者
2. **接口**
   - `POST /api/videos/{id}/cleanup`：幂等跳过（返回 `skipped_reason`，`deleted:[]`）；无拆解记录返回 409 不误删
   - `POST /api/videos/{id}/media-policy`：改策略并立即按新策略清理
   - analyse 接口在 `analysis.status == "done"` 后自动触发 `cleanup_video(auto_trigger=True)`，失败只记 warning 不阻塞响应
3. **前端降级**
   - VideoBreakdown 无本地源时渲染降级组件 `NoLocalMediaBox`；`_video_public.media` 序列化 `video_url / media_policy / cleaned / audio_track_url` 供前端判断

### 本轮完成（数据库 PG → SQLite）

1. **ORM 类型替换（`app/models.py`）**
   - `JSONB → JSON`、PG `ARRAY → JSON`、PG `UUID → 本地 Uuid 子类`（str 归一：DB 内 id 存为无连字符 hex，接口 JSON 仍按 UUID 格式回显）
2. **连接与引擎（`app/core/config.py` / `app/db.py`）**
   - `DATABASE_URL` 默认 `sqlite+aiosqlite` 指向 `backend/data/frames.db`；PG 旧连接保留为 `PG_LEGACY_URL`
   - db.py 开启 SQLite PRAGMA；requirements 增加 `aiosqlite`
3. **迁移链路**
   - Alembic 新基线 `d1a0b2c3e4f5`（保留链，不依赖旧 PG 上游）；21 张业务表 + alembic_version 已在新库
   - 迁移脚本：`backend/scripts/migrate_pg2sqlite_20260909.py`（幂等：`--jsonl-dir` 已存在跳过导出；目标已有数据默认拒绝）
   - PG 全量归档底：`backend/data/backup/frames_pg_20260909_190635.dump` + `jsonl_20260909_190635/`
   - 常用命令：
     ```bash
     cd ~/Projects/frames/backend
     python -m scripts.migrate_pg2sqlite_20260909            # 幂等重跑（跳过已导出）
     python -m scripts.migrate_pg2sqlite_20260909 --fresh    # 目标库已有数据时强制重建
     python -m scripts.migrate_pg2sqlite_20260909 --no-archive  # 跳过 pg_dump 归档
     ```
   - 回滚基线：PG 侧数据以 dump 归档可恢复；SQLite 库本身为本地文件可直接备份

### 本轮完成（打包方案文档 + 脚本目录骨架）

1. `docs/11-local-packaging.md`：本地单体打包方案（含 §5 前端 dist 后端托管拍板、§5.2 WHISPER_MODEL_PATH/WHISPER_MODEL 修正）
2. `backend/scripts/run_app.sh`（uvicorn 启动 + PID 防重入）、`stop_app.sh`（优雅停止）、`prepare_sidecars.sh`（sidecar 预检：本机 ffmpeg/ffprobe OK、**yt-dlp MISS** 需补装）
3. 占位目录：`backend/data/sidecars/`、`backend/data/logs/`

### 本轮完成（回归验证 + A 真实闭环·SQLite 免费档）

1. **③ 阶段回归（先于 A）**：dev 后端 SQLite（PID 75044, 127.0.0.1:8000）
   - `75120be3` cleanup 幂等 200（skipped_reason=已清理, deleted:[]）；`c9b5d02b` 无拆解 409 未误删
   - elements/products ilike 路由 200；前端 `tsc -b` 零错误
2. **A 真实闭环（2026-09-09 21:17–21:20）**
   - 候选：B站公开视频 BV11qZ3Y8EX3《这个女人有点坏#动画演示…》（48.2s，480P 原片 4.36MB）
   - bilix 建档下载 12.6s → 新 video `569860b9c9834561ba50186b8a5cc87b`；L1-L5 全部 done（analysis `560d19ea539444488cbe890017d4f6b7`，failed_layers=0，落库 segments=9 / notes=15 / elements=5 / layers=5）
   - 耗时：建档 21:17:01–14；素材采集（转写/抽帧/视觉/ASR校对）约 44s；AI 五层拆解 21:18:11–21:20:37（约 146s）；curl 总 190s
   - **auto 自动清理生效**（done 同一时刻触发）：原片 mp4 与 audio_16k.wav 已删；保留 audio_track.m4a 776.5KB + manifest.json + 16 帧 jpg（工作目录残留合计约 1.4MB）；raw_files：cleaned=true、cleaned_at、retained_files=["audio_track.m4a"]、video_path=null
   - GET /api/videos/{id}：`media.video_url=null`、`audio_track_url=/media/url_7d414f9eee5451bd/audio_track.m4a`、`media_status=ready`
   - 素材目录体积：下载前 653M → 闭环后约 655M（仅新增 ~1.4MB 产物，大原片已回删）
   - **全程免费档确认**：本时段 ai_usage_logs 仅 `zhipu`（glm-4-flash：breakdown×4、asr_proofread×1、video_category_classify×1；glm-4v-flash：vision×4），未触达 moonshot/deepseek 付费档；element_mix / product autofill 属可选付费环节，本链路未触发，无必要跳过

### 断点重启步骤（本地单体方向，当前生效）

```bash
cd ~/Projects/frames/backend
bash scripts/run_app.sh            # 或指定端口 bash scripts/run_app.sh 8000；日志 data/logs/uvicorn.log
bash scripts/stop_app.sh           # 优雅停止
bash scripts/prepare_sidecars.sh   # 预检 ffmpeg/ffprobe/yt-dlp（2026-09-10 起查找顺序含 .venv，已全绿）
```

### 遗留风险与待办

- [x] 【2026-09-10 已修复】WAL 已开启（db.py 已注册 `journal_mode=WAL` / `synchronous=NORMAL` / `busy_timeout=5000`，实测 `journal_mode=wal`）仍偶发 `database is locked`（本次 A 闭环中 ai_usage_logs 落账丢 1 条，业务 200 正常）→ 根因在 `ai.py` 用量记账用独立 `SessionLocal()` 会话 commit，失败被裸 `except` 静默吞掉仅留 warning → 已按「失败重试 1 次」落地：`_log_usage` 退避 0.2s 重试，重试仍失败记 ERROR（含 scene/ref 便于对账）不再静默，详见下方 2026-09-10 小节
- [ ] 前端 NoLocalMediaBox 降级未做浏览器交互冒烟（tsc/静态分支已过）
- [x] 【2026-09-10 已修复】本机 yt-dlp 预检红灯：实为 `.venv/bin/yt-dlp` 已装但不在 PATH，`prepare_sidecars.sh` 查找顺序已扩为 `PATH → sidecars → .venv`，预检全绿
- [ ] 元素变异/组合入口（E 域飞轮）未做
- [ ] demo001（example.com 演示数据）等历史演示记录仍留库，可视需要清理
- [ ] Windows 实机/VM 冒烟未做（阶段④另一半）：逐项待测清单见 [12-windows-smoke-checklist.md](12-windows-smoke-checklist.md)

---

## 2026-09-10（阶段④ Mac 侧：selfcheck 落地 + 记账丢账修复 + yt-dlp 预检全绿）

### 本轮完成

1. **④ selfcheck 自检脚本落地**
   - 新增 `backend/scripts/selfcheck.py`（纯标准库、跨平台、无三方依赖）：11 项检查 —— Python 运行时 / 依赖完整性 / ffmpeg / ffprobe / yt-dlp / whisper 模型 / demucs 模型 / 数据库文件（含 WAL 状态与表数）/ 素材目录可写 / 前端 dist / 端口占用（含 PID）/ 云薄服务连通性（可选，`--skip-net`）；支持 `--json`、`--port`
   - 退出码语义按 §7 落地：`0`=全部 PASS；`1`=有 WARN（可选组件缺失，如 demucs 未装）；`2`=有 ERROR（关键项缺失 / 端口被占）
   - 新增入口包装 `run/selfcheck`（macOS/Linux，已 `chmod +x`；解释器优先级 `.venv/bin/python` → `python3` → `python`）与 `run/selfcheck.bat`（Windows 版，**未实机验证**）
   - Mac 实测：`run/selfcheck --port 8123 --skip-net` → 10 项 OK + demucs 可选 WARN，退出码 **1**；默认端口 8000 → `[ERROR] 8000 已被占用（PID 75044）`，退出码 **2**；`--json` 结构正常
2. **ai.py 用量记账丢账修复**
   - 改动：`_log_usage` 新增 `USAGE_LOG_RETRY_DELAY=0.2` / `USAGE_LOG_MAX_ATTEMPTS=2`，独立会话 commit 失败时退避 0.2s **重试 1 次**；重试仍失败记 **ERROR** 级日志（含 scene/provider/ref 便于对账 + `exc_info`），不再被裸 `except` 静默吞掉；仍不上抛、不阻塞业务
   - 验证脚本（本次中间产物 `temp/test_ai_usage_logging.py`，用**独立临时 SQLite 库**，不污染 dev 库）三用例全 PASS：① 40 路并发 → 落库 40 条；② 模拟首次 `database is locked` → 重试后成功且仅落 1 条；③ 始终失败 → 不抛异常 + 恰好 1 条 ERROR
   - 生效条件：需重启后端进程（当前 dev 服务仍是旧代码，本次未代为重启）
3. **yt-dlp 预检全绿**
   - 根因：yt-dlp 实际已装于 `backend/.venv/bin/yt-dlp`（2026.08.19），但不在 PATH，`prepare_sidecars.sh` 只查 PATH/sidecars → 误报 MISS
   - 修复：`prepare_sidecars.sh` 查找顺序扩为 `PATH → sidecars → $BACKEND_DIR/.venv/bin`（Windows 为 `Scripts`），OK 行附带版本号；并支持从 `backend/.env` 回退读 `WHISPER_MODEL_PATH` / `WHISPER_MODEL`（不覆盖已有环境变量）
   - 结果：`bash backend/scripts/prepare_sidecars.sh` → ffmpeg 8.1.1 / ffprobe 8.1.1 / yt-dlp 2026.08.19 全 OK，`== 预检完成：全部就绪 ==`，退出码 0
   - `backend/requirements.txt` 补齐此前漏登记的依赖：`httpx==0.28.1`、`yt-dlp==2026.8.19`、`bilix==0.18.9`、`faster-whisper==1.2.1`（httpx / yt_dlp 为模块级 import，漏装即启动失败）
4. **Windows 冒烟待测清单**：新增 `docs/12-windows-smoke-checklist.md`（W1–W18 待测项 + 结果记录表 + 待补产物清单）；`00-README.md` 文档导航与 `11-local-packaging.md` ④ 小节均已加进展标注

### 断点重启步骤（增量）

```bash
cd ~/Projects/frames
run/selfcheck --port 8000                  # 启动前自检；退出码 0/1/2
run/selfcheck --json | jq .                # 机器可读输出
bash backend/scripts/prepare_sidecars.sh   # sidecar 预检（现已全绿）
# 记账修复需重启后端才生效：
bash backend/scripts/stop_app.sh && bash backend/scripts/run_app.sh
```

### 遗留风险与待办

- [ ] Windows 实机/VM 冒烟整体未做（阶段④另一半），逐项见 `docs/12-windows-smoke-checklist.md`；`run/selfcheck.bat`、`run/start.bat`、`prepare_sidecars.ps1` 属待补产物
- [ ] 记账修复仅经临时库三用例验证，未在真实并发采集负载下复测（建议下次 A 闭环时比对 ai_usage_logs 条数与实际调用数）
- [ ] demucs 未安装 → selfcheck 恒有 1 条 WARN（退出码 1）；如需 BGM 分离再按需下载
- [ ] NoLocalMediaBox 降级浏览器交互冒烟未做；E 域飞轮未做；demo001 历史演示记录待清理

## 2026-09-10（阶段④ Mac 侧续：类 app 启动体验落地）

目标：让「帧间」在 macOS 与 Windows 上都能「像用 app 一样」启动——双击入口即开界面，关窗口服务不退出。

### 本轮完成

1. **后端单端口托管前端 dist**（`backend/app/main.py`）
   - 新增 `FRONTEND_DIST` 解析（默认 `<项目根>/frontend/dist`）+ `SpaStaticFiles`（未命中路径回退 `index.html`，前端深链刷新不再 404）
   - `index.html` 存在 → 挂载 `/` 为完整界面；缺失 → 优雅降级为「仅 API」（根路径返回 JSON `web_ui:false`，日志明确提示），不阻塞 API
   - 修复挂载顺序隐患：前端挂载必须在所有 API 路由之后注册，否则 `mount("/")` 会吞掉 `/api/*`；`/api/health/db` 等接口先注册，实测未被吞
2. **跨平台启动器**（`backend/scripts/launcher.py`，纯标准库，无第三方依赖）
   - 子命令 `start` / `stop` / `status` / `restart`；参数 `--port` / `--no-browser` / `--timeout` / `--json`
   - start：防重入（已在运行且健康则不重复拉起）→ 依赖 import 检查（给 pip 指引）→ 端口 bind 探测（占用时报 PID，macOS `lsof` / Windows `netstat -ano`）→ 前端产物检查 → 后台拉起（macOS `start_new_session`；Windows `DETACHED_PROCESS|CREATE_NEW_PROCESS_GROUP|CREATE_NO_WINDOW`）→ 健康等待（轮询 `/` 直到 200，超时打印日志尾部并回收进程）→ 自动开浏览器
   - stop：优雅（SIGTERM / `taskkill /T`）→ 12s 未退升级强制 → 确认端口释放 → 清理 PID/元信息；失败如实报告不谎报成功
   - 运行时文件按端口隔离：默认 8000 沿用 `data/uvicorn.pid`（与既有 `run_app.sh` / `stop_app.sh` 互通，可互相启停）；非默认端口用 `data/uvicorn-<port>.pid` / `.meta.json` / `logs/uvicorn-<port>.log`
   - 环境变量：`FRAMES_PORT` / `FRAMES_OPEN_BROWSER` / `FRONTEND_DIST` / `FRAMES_DATA_DIR`
3. **双平台双击入口 + 终端入口**
   - macOS：`run/启动帧间.command`、`run/停止帧间.command`（自动 `cd` 到项目根、`--open-browser`、结束保留窗口并提示「可关闭窗口，服务不退出」）
   - Windows：`run/启动帧间.bat`、`run/停止帧间.bat`（先 `chcp 65001`、`PYTHONIOENCODING=utf-8`，避免中文乱码；CRLF 换行）
   - 终端：`run/start` / `run/stop` / `run/status`（自选 `.venv` 解释器，参数透传）
4. **文档**
   - 新增 `docs/13-app-launcher.md`（交付物 / 用法 / 单端口设计 / 启动器行为 / 运行时文件与端口约定 / Mac 实测记录 / 排障）
   - `docs/12-windows-smoke-checklist.md`：W13/W14 改写为指向新入口，新增 W19 双击启动、W20 双击停止、W21 防重入与多实例、W22 中文路径与控制台编码；§3 待补产物更新、§4 补 mac 侧已就绪项
   - `docs/00-README.md`：导航加 13；"快速启动"改为三档（双击 / 终端 / 开发模式）
   - `docs/11-local-packaging.md`：② 小节补进展标注，④ 小节进展标注补启停入口

### 实测记录（macOS 本机，全部用空闲端口，未触碰 8000 上运行中的服务 PID 7558）

| 场景 | 结果 |
|---|---|
| `run/status` 未运行 | 退出码 1，提示端口空闲 |
| `run/start --port 8123` | 依赖/端口/前端检查通过，PID 9847，2.5s 就绪，退出码 0 |
| `curl /` | 200 `text/html`，返回 dist 的 index.html |
| `curl /deep/route`（前端深链） | 200 `text/html`，回退 index.html |
| `curl /assets/index-*.js` | 200 `text/javascript`，347 KB |
| `curl /api/health/db`、`/api/videos` | 均 200 JSON（验证未被静态挂载吞掉） |
| 再次 `run/start` | `已在运行 PID=9983`，未重复拉起（防重入生效） |
| `FRONTEND_DIST=/tmp/nonexistent-dist-xyz` 启 8124 | WARN 降级为仅 API，根路径返回 `web_ui:false` JSON |
| `FRAMES_PORT=8126 bash run/启动帧间.command` | 双击链路跑通：检查 → PID 10099 → 就绪 2.1s → `浏览器 已打开界面`，退出码 0 |
| `run/stop --port 8123/8124/8126` | 均「停止完成 端口已释放」，PID/元信息清理，退出码 0 |
| `run/start --port 8127` 复测 + `status --json` | 退出码 0；JSON 含 `running/healthy/pid/port/url/started_at/log`；`stop` 后端口释放 |
| 既有 8000 服务 | 全程 200，`data/uvicorn.pid` 仍为 7558，未被重启或覆盖 |

### 断点重启步骤（日常使用，增量）

```bash
cd ~/Projects/frames
run/start            # 或双击 run/启动帧间.command；界面 http://127.0.0.1:8000/
run/status           # 状态（退出码 0=运行中）
run/stop             # 或双击 run/停止帧间.command
```

### 遗留风险与待办

- [ ] Windows 侧双击入口与启动器**未实机验证**：新增 W19–W22 见 `docs/12-windows-smoke-checklist.md`（原「`run/start.bat` 待补产物」条目作废，已由 `run/启动帧间.bat` + `launcher.py` 覆盖）
- [ ] 当前 8000 上运行的仍是旧代码（PID 7558），**单端口托管需重启后端才生效**：本次未代为重启
- [ ] `frontend/dist` 为既有构建产物，本次未重新 `npm run build`；后续前端改动需重新构建后单端口才可见
- [ ] macOS 首次双击 `.command` 可能被 Gatekeeper 拦（右键→打开即可），交付说明需补该指引

---

## 2026-09-10（阶段⑤：批量提交与进度常驻 + 免费档统一 + 短视频密帧 + 简体化 + 两处严重缺陷修复）

目标：拆解链路「批量可提交、进度看得见、模型可切免费档、文案为简体、短视频看得更细」，并把上一轮实测暴露的两个严重缺陷（ASR 提示词回声、素材复用短路）彻底修掉。

### 本轮完成

1. **批量提交 + 进度常驻可见**
   - 新增 `POST /api/videos/batch-analyse`（一次最多 20 条链接，逐条建档并排队拆解）与 `GET /api/analyses/active`（返回进行中任务的阶段/百分比）
   - 进度落库到 `analyses.meta["progress"]`（`stage/message/layer/pct/updated_at`），前端 `JobMonitor.tsx` 以 `POLL_MS=3000` 轮询常驻展示，切页不丢
   - 修复 `app/routers/videos.py` 缺 `UTC` 导入导致批量接口 500 的问题
2. **模型档位统一到 `runtime_config`（默认免费档）**
   - 逻辑档位 → 内部 alias：`free_flash → flash`（默认，免费/低价，如 glm-4-flash）、`paid_pro → pro`（进阶，需余额）
   - `DEFAULT_CREATION_MODEL = DEFAULT_BREAKDOWN_MODEL = "free_flash"`；`creations` / `videos` / `breakdown_jobs` 一律改用 `creation_alias()` / `breakdown_alias()`，不再硬编码模型名
   - 现状：`backend/data/runtime_config.json` 不存在 → 走默认免费档（创作台与五层拆解均为 `flash`）
3. **短视频（< 5 分钟）密集关键帧**（`app/media/frames.py`）
   - `SHORT_MAX_S = 300.0`、`SHORT_MAX_FRAMES = 120`；短视频目标帧数 `budget = min(120, round(duration / 1.5))`，间隔档 `iv = (0.8, 1.6, 4.0)`（长视频维持 90 帧上限不变）
   - `FRAMES_POLICY_VERSION = 2` 并写入 `frame_plan.policy_version`，供素材复用判定（见第 6 条）
4. **文案获取方式核实与简体化**
   - 结论：文案主链路是 **ASR**（本地 `faster-whisper-small` 离线转写，`data/models/faster-whisper-small`），平台字幕与画面 OCR 仅作兜底；实际来源落库到 `videos.subtitle_source`（本轮实测为「语音转写」）
   - 简体化双保险：ASR 输出逐段 `to_simplified()` 源头统一（`trad_fixed` 计数入日志）+ 拆解前置 `self_check.traditional_in_asr` 自检
5. **缺陷① 修复：ASR 提示词回声（真实语音被冲掉）**
   - 根因：上一轮为压繁体给 whisper 加了 `initial_prompt="请使用简体中文转写。"`，真实音频出现**整段退化为提示词回声**（实测 13 段全为「请使用简体中文转写。」），语音内容完全丢失
   - 修复（`app/media/transcribe.py`）：**移除 `initial_prompt`**；改为「多配置解码 + 退化后置检测 + 回退」——`_DECODE_ATTEMPTS = (novad, vad)` 逐个解码，`degenerate_reason()` 命中 `prompt_echo` / `repeated` / `empty` / `too_short` / `too_sparse` 即判退化，取「首个未退化」；全部退化时取字数最多者（宁可保留嘈杂文本，也不返回空/回声）
   - `ASR_PIPELINE_VERSION = 3`，结果附带 `pipeline_version / decode / degenerate`，退化时在拆解链路记 warning 并在进度文案带解码标识
6. **缺陷② 修复：素材复用短路（新策略对旧链接不生效）**
   - 根因：`media_prep.ensure_media()` 只要 `transcript + frames + media_status=ready` 就整体复用，抽帧策略或转写管线升级后旧素材不被重采 → 密帧与简体化对已拆解过的链接"看起来没生效"
   - 修复（`app/services/pipeline.py` + `app/services/media_prep.py`）：manifest 落 `media_spec = {frames_policy_version, asr_pipeline_version}`；`ensure_media()` 先跑 `media_spec_stale()` 比对当前版本，过期即**不再复用**、改走 `run_pipeline()` 重采集（重下载/重抽帧/重转写），并把原因写入 `manifest.recollect_reason`；版本一致时仍走复用，避免每次白白重采
7. **验收文档**：本节 + `00-README.md` 现状说明。

### 验收记录（macOS 本机 · 真实短视频 · 免费档 flash）

**A. 真实重跑（问题原案视频：抖音「这才是教育的意义」，114.7s）**

| 项目 | 重跑前 | 重跑后 | 结论 |
|---|---|---|---|
| 触发原因 | — | `recollect_reason = 抽帧策略 vNone → v2` | 短路已解除，版本过期即重采 |
| 关键帧 | 32 帧 | **52 帧**（`mode=short`，`budget=76`，`iv=[0.8,1.6,4.0]`，`policy_version=2`） | 短视频密集档真实生效 |
| 帧构成 / 视觉描述 | — | motion 10 / steady 19 / static 15 / anchor 8；**52/52 帧有视觉描述** | 密帧未牺牲画面理解 |
| ASR | 13 段全为提示词回声（内容丢失） | **10 段 / 237 字，`decode=novad`，繁体段 0，`degenerate` 空** | 回声已消失，真实语音找回且为简体 |
| 文案来源 | — | `subtitle_source = 语音转写`；`self_check = {traditional_in_asr:0, text_source_mode:'asr_only', frame_mode:'short', frame_mode_ok:true}` | ASR 主、OCR 兜底的判定成立 |
| 重跑耗时 | — | 117.8s（下载→转写→抽帧→视觉→音频） | 可接受 |

**B. 五层拆解完整性（同一视频，模型 `flash` 免费档）**

| 层 | 条目数 | 字符数 | truncated | repaired | rounds | finish_reason |
|---|---|---|---|---|---|---|
| L1 | 1 | 439 | false | false | 1 | stop |
| L2 | 7 | 451 | false | false | 1 | stop |
| L3 | 7 | 1244 | false | false | 1 | stop |
| L4 | 13 | 1626 | false | false | 1 | stop |
| L5 | 7 | 1334 | false | false | 1 | stop |

- 整体 `status=done`，耗时 133s；五层内容全为简体（逐层 `has_traditional` 均为 False），无截断、无补写、无二次重试
- 复跑负向验证：版本号一致时第二次调用直接 `reuse/done 复用已有素材`（不再重复下载/转写/抽帧），确认修复未引入"每次必重采"

**C. 单元与分支自检（不联网、不调模型）**

- `temp/unit_checks.py`：13 项全部 PASS —— 回声整段/单段→`prompt_echo`；单句复读→`repeated`；正常口播与 2 段文本**不误杀**；`empty` / `too_short` / `too_sparse` 边界；老素材（无版本）与仅 ASR 落后 → 判过期，版本一致 → 可复用；源码中已无 `initial_prompt=` 传参
- `temp/fallback_checks.py`：12 项全部 PASS —— A 首选退化→自动回退次选并采信真实文本（且不含回声）；B 两档全退化→取字数最多者且非空；C 首选未退化→仅解码一次，不浪费算力

### 断点重启步骤（增量）

```bash
cd ~/Projects/frames
# 本轮为后端代码改动，需重启后端（8000 上若仍是旧进程，先停后起）
run/stop && run/start
# 或：bash backend/scripts/stop_app.sh && bash backend/scripts/run_app.sh
# 重新验证某条历史视频（会因版本过期自动重采）：
#   打开拆解页粘贴原链接 → 提交；进度在页面顶部常驻（3s 轮询）
```

> 中间产物（未纳入仓库）：`temp/rerun_check.py`（真实重跑+五层验收）、`temp/unit_checks.py`、`temp/fallback_checks.py`、`temp/report_echo114.json`、`temp/report_analysis.json`、`temp/rerun_echo114*.log`。

### 遗留风险与待办

- [ ] **后端需重启才生效**：本轮改动（transcribe / frames / pipeline / media_prep / videos / runtime_config）在当前 8000 进程上尚未加载，重启后新旧视频才会走新判定
- [ ] **前端 dist 未重建**：批量提交与进度常驻属前端改动，需 `cd frontend && npm run build` 后单端口界面才可见
- [ ] ASR 仍有同音错字（实测「宁感」应为「灵感」、「客厂」应为「课堂」），属 small 模型能力上限；拆解层可容错，如需更准可切 medium（耗时上升）——暂不改默认
- [ ] 「退化回退」分支仅以构造用例 + 单测覆盖，真实链路本轮未再触发退化（即修复已生效但缺真实故障样本复现）；后续再遇回声请留存原片
- [ ] 素材版本升级会导致**历史链接下次打开时全量重采一次**（预期行为）；如需豁免个别视频，手工在 manifest 写入 `media_spec` 即可
- [ ] 长视频仍为 90 帧上限（本轮未调整）；批量的并发度与失败重试策略未做压测

## 2026-09-10（阶段⑥：可切换免计费模式）

背景：充值渠道尚未上线，创作台/拆解在余额不足时被 402 拦死，用户实际用不了；诉求是**先放开点数限制、等充值上线后再启用计费**。结论：加一个**默认开启的免计费开关**，一个配置即可双向切换，业务代码零改。

### 本轮完成

1. **单一开关落地（一处配置）**
   - `app/services/runtime_config.py`：新增 `billing_enforced`（默认 `False` = 免计费模式）+ `DEFAULT_BILLING_ENFORCED = False`、`billing_enforced()`、`set_billing_enforced()`，并纳入 `public_config()` 输出
   - 取值优先级（注释已写入代码）：环境变量 `FRAMES_BILLING_ENFORCED`（`1/true/yes/on`）> `backend/data/runtime_config.json` 的 `billing_enforced` > 默认 `False`
   - **改 JSON 即生效，无需重启**（每次调用现读文件）；环境变量优先，便于部署侧一键强制计费
2. **覆盖全部拦截点（无遗漏）**
   - 所有拦截都收敛在 `app/services/billing.py` 两个函数，故只需改这一处即可覆盖全部调用方：
     - `precheck()`：免计费模式直接返回 `(账户, 点数)`，不做余额校验、不做日上限校验（未知动作仍 400）
     - `consume()`：免计费模式**只记流水不扣余额**——`type="free_usage"`、`points=-N` 作用量口径、note 追加「（免计费模式，未扣点）」；不动 `balance_points` / `total_consumed_points`
   - 免计费流水**不计入**「今日已用」与日消费上限（`_today_consumed` 只统计 `type="consume"`），避免切回强制计费后被免计费期间的用量顶到 429
   - 调用方清单（均自动受益，未改动业务代码）：`creations.py`（chat/guide_generate/versions）、`elements.py`、`ai.py`、`products.py`、`videos.py`（批量提交预检 + 任务内扣点）、`breakdown_jobs.py`
   - AI 真实调用与「AI 用量账」（`AiUsageLog`）不受影响，照常记录
3. **可观测性**
   - `GET /api/billing/account` 新增 `billing` 区块：`{enforced, mode: free|enforced, free_used_points, note}`
   - 前端 `BillingPanel.tsx`：免计费时徽标由「模拟计费」变「**免计费模式**」，并显示「免计费期间已用 N 点（未扣余额）」；流水类型新增「免计费」标签；`api.ts` 补 `billing?` 类型
   - `frontend/dist` 已重建（单端口 `:8000` 直接可见，无需另起前端）

### 验收记录（macOS 本机 · 真实链路 · 免费档 flash）

测试前置：账户余额 **5 点**（150 点的文档稿 / 10 点的对话在强制计费下必然 402），即真实「点数不足」现场。

| # | 动作 | 结果 | 结论 |
|---|---|---|---|
| 1 | `GET /api/billing/account` | `billing.mode=free`，balance=5 | 默认免计费已生效 |
| 2 | `POST /api/creations/guide/preview`（真实 AI，14s） | 200，返回「创作任务卡 + 核心策略速览」 | 速览链路放行 |
| 3 | `POST /api/creations/guide/generate`（真实 AI，14s） | 200，`creation_id=ac524817-c4cf-405d-88b3-81594bd7197a`，**落库 8 章**（brief/strategy/script/shooting/editing/publish/checklist/sources） | **真出稿成立**（原会被 402 拦） |
| 4 | `GET /api/creations/{id}` 复核 | 200，8 个 guide_* 资产齐全 | 稿件真实落库 |
| 5 | 账户复核 | balance **5 → 5**（未扣）；`free_used_points = 150`；流水 `free_usage / guide_generate / -150 / 创作指南生成（便携咖啡杯种草短视频）` | 只记用量、不扣余额 |
| 6 | `POST /api/creations/chat`（10 点动作） | 200，正常回复 | 对话拦截点放行 |
| 7 | 开关切 `billing_enforced=true`（仅改 JSON，不重启）后 `chat` | **402** `INSUFFICIENT_POINTS`（required=10, balance=5） | 一处配置即可切回强制计费 |
| 8 | 切回 `false` 后 `chat` | 200 放行；`free_used_points` 继续累加（170 → 180） | 可逆、即时生效 |
| 9 | `GET /api/ai/usage/summary` | 今日 49 次调用 / 149,577 tokens / 估算 ¥0.266 | AI 用量账照常记录 |

测试脚本（中间产物，未入库）：`temp/e2e_free_billing.py`（步骤 1–8 全自动，含开关双向切换）。

### 断点重启步骤（增量）

```bash
cd ~/Projects/frames
# 后端代码改动：需重启后端加载新的 billing/precheck/consume
run/stop && run/start --no-browser
# 前端改动已构建（BillingPanel/api.ts）：
# cd frontend && npm run build   # 已执行，dist 已更新

# 切换到「强制计费」（充值上线后执行，二选一）：
#   ① 改 backend/data/runtime_config.json → "billing_enforced": true（免重启）
#   ② 或启动时 export FRAMES_BILLING_ENFORCED=1（优先级更高，适合部署）
```

### 遗留风险与待办

- [ ] **免计费期间的成本敞口**：放开校验后，日消费上限与余额拦截均不生效，AI 真实成本由自己承担；若担心被刷，可临时 `FRAMES_BILLING_ENFORCED=1` 恢复拦截，或后续给免计费模式加"每日 AI 调用次数上限"（`AiUsageLog` 已具备统计能力，改动量小）
- [ ] 免计费流水 `type="free_usage"` 为新增类型：老数据/其他统计口径若有按 `consume` 汇总的地方不会受影响，但**导出/对账时需把 `free_usage` 与 `consume` 合并看待**才是完整用量
- [ ] 账号余额仍是 5 点（未充值）：切回强制计费前需先充值，否则创作台会立刻 402
- [ ] 前端 `TX_TYPE_LABEL` 之外的未知流水类型仍按原始 type 展示（当前仅 `free_usage` 新增，已补标签）
- [ ] 免计费期间的「今日已用」恒为 0（设计如此），页面上以「免计费期间已用 N 点」单独体现，避免误读为没消耗

*（内容由AI生成，仅供参考）*

## 2026-09-11（阶段⑦：拆解链路两处阻塞修复 —— ffprobe 缺失 + ASR 逐句时间戳与纠错）

### 本轮完成

1. **ffprobe 缺失导致整链路 failed（严重）**
   - 根因：macOS Homebrew 的 ffmpeg/ffprobe 在 `/opt/homebrew/bin`，而访达/launchd 启动的进程 PATH 仅 `/usr/bin:/bin:/usr/sbin:/sbin`；媒体模块以裸命令名调用，抛 `Errno 2: 'ffprobe'`，拆解 `failed` 且无层产物。
   - 修复：新增 `backend/app/media/ffbin.py`（环境变量 → `shutil.which` → 常见安装目录解析绝对路径，命中目录前置写回 PATH 供 yt-dlp/bilix 复用；含 `binary_report()` 诊断与 `ensure_binaries()` 采集前自检）；`audiofeat/cleanup/downloader/frames/pipeline/transcribe` 六处改引用常量；`pipeline.py:73` 采集前自检。
2. **ASR 段落时间戳全为 None + 同音错字**
   - 修复：`transcribe.py` 升 `ASR_PIPELINE_VERSION=4`，新增 `format_ts` / `segments_to_timestamped_text` / `parse_timestamped_text`；`proofread_segments` 改为分批（8 行）+ 上下文 + 护栏，新增等长替换片段收集（`_collect_fix_spans`，仅 2~4 字，排除单字替换）与同词传播（`_PROOFREAD_PROPAGATE`）补齐漏改；`pipeline.py` 落 `transcript.timestamped_text`；`media_prep.py` 新增 `normalize_subtitle_text` / `subtitle_for_breakdown`。
   - 拆解主链路（`routers/videos.py`、`services/breakdown_jobs.py`）改用逐句带时间戳素材。
3. **L3 时间段锚点全为 0（附带修复）**
   - 契约加硬（`prompt_contract.py`：必须给 `lines=[起,止]` 行号区间 + 真实毫秒，声明服务端回查原文纠正）；新增 `services/time_anchor.py` 做确定性回填（行号区间 → 原话匹配 → 顺序估算 + 单调校正，不编造）；`analysis.py` L3 加行号并在成功路径回填，统计写入 `meta.layer_quality["3"].time_anchor`。

### 验收记录（macOS 本机 · 纯净/受限 PATH · 真实短视频 · 免费档 flash）

| # | 动作 | 结果 | 结论 |
|---|---|---|---|
| 1 | `PATH=/usr/bin:/bin` 下 `ffbin.binary_report()` | ffmpeg/ffprobe 均解析到 `/opt/homebrew/bin`，`ok=true` | 裸 PATH 不再丢二进制 |
| 2 | 同环境直接调 ffprobe 探时长 | rc=0，`114.729002` s | 真实可执行 |
| 3 | `pipeline._ffprobe_duration` | `114729 ms` | 与 ② 一致 |
| 4 | 端到端 `ensure_media`（同一样本） | `duration_ms=114729`、`asr_version=4`、无异常 | 采集链路通 |
| 5 | ASR 段落时间戳 | `segments=10 缺时间戳段=0` | 逐句可保存 |
| 6 | 纠错 | `proofread 采纳=5`；「客厂→课堂」「宁感→灵感」「腐脂→构思」生效；`残留错字命中=[]` | 同音错字修正 |
| 7 | 逐句文本 | `timestamped_text` / `subtitle_text` 每行带 `[mm:ss.s-mm:ss.s]` | 原话与时间并存 |
| 8 | L3 单层实测（flash） | 模型给 `lines=[1,2]/[3,5]/[6,9]`；回填 `by_lines=3, estimated=0`；落库 `[0-37300]/[43300-55300]/[55300-87300]`，有效锚点 3/3、起点递增、均在时长内 | 时间锚点真实 |
| 9 | 时间锚点单测（7 组） | 行号命中/原话匹配/顺序估算/保留合法/单调校正/无时间戳不伪造 | 全部通过 |

测试脚本（中间产物，未入库）：`temp/_test_ffbin.py`、`temp/_test_time_anchor.py`、`temp/_probe_l3.py`、`temp/_e2e_media.py`。

### 断点重启步骤（增量）

```bash
cd ~/Projects/frames
run/stop && run/start --no-browser     # 后端改动：新增 ffbin/time_anchor，需重启加载
# 如需重跑校对：对既有视频重新执行 ensure_media（asr_version 由 3 升 4 会触发重转写）
# 若 ffmpeg 装在非默认目录：export FRAMES_FFMPEG_DIR=/path/to/bin 或 FRAMES_FFMPEG_BIN=/path/to/ffprobe
```

### 遗留风险与待办

- [ ] **L3 分段粒度不稳**：同一 114s 样本，flash 返回段数在 1~4 间波动；回填只保证时间真实，段数契约（6~14 段）未做强校验，建议加段数下限校验 + 自动重试
- [ ] **情绪曲线仍是定值**：实测 `emotion_curve` 全为 `5.0`，与"连续曲线 + 作为创作复用关键"目标不符，需在 L3/L4 契约与落库结构中专项处理
- [ ] **组合模板忠实还原 / 物理分表 / 旧数据清理**尚未开始，依赖本次修好的时间锚点数据先行
- [ ] 免费档模型对长 JSON 仍可能截断（已有 `complete_json` 续写兜底），L4/L5 未在本轮复测

*（内容由AI生成，仅供参考）*
