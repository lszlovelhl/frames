---
AIGC:
    Label: "1"
    ContentProducer: 001191440300708461136T1XGW3
    ProduceID: ce0d790a7b004233c1097246b343160b_4acc67b1a81911f18ba4525400f8a581
    ReservedCode1: mJSYiPwQavauOVK6+3JKMYm2JDrwv8jpHmqrOe88icygvti3wF38NzXyAVPaLXT9MU1EceUW8T+4rsrq2s5YgTIU0uQLxieAUcO0uX4WvqNCf53ot7/ISTfQdCfHP4uPKQP6PqNVHsG4Ye2R+FQHYUrRYvILYmLS5FT+tq+OO4v5YOXgfMIf/ATQzNU=
    ContentPropagator: 001191440300708461136T1XGW3
    PropagateID: ce0d790a7b004233c1097246b343160b_4acc67b1a81911f18ba4525400f8a581
    ReservedCode2: mJSYiPwQavauOVK6+3JKMYm2JDrwv8jpHmqrOe88icygvti3wF38NzXyAVPaLXT9MU1EceUW8T+4rsrq2s5YgTIU0uQLxieAUcO0uX4WvqNCf53ot7/ISTfQdCfHP4uPKQP6PqNVHsG4Ye2R+FQHYUrRYvILYmLS5FT+tq+OO4v5YOXgfMIf/ATQzNU=
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
- [ ] 元素库：查询 / 采纳 / 纠错 API 与页面（质量自循环）
- [ ] 创作单步 API（选题建议 / 脚本 / 拍摄指导）→ 跑通 MVP「拆解 → 创作」链路
- [ ] 回填蓝图 v0.1 待补全项（v1 表结构、提示词资产细节、部署方式）

### 关键路径备忘

- 项目根：`~/Projects/frames`
- 蓝图文档（会话产出）：`~/Library/.../workspace/conv_*/output/梁龙科技-帧间重建蓝图-v0.1.md`（核心方法论已并入 docs/01）
- 连接串默认：`postgresql+asyncpg://zhuolittlelong@localhost:5432/frames_dev`
*（内容由AI生成，仅供参考）*
