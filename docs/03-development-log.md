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
