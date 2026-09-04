# 帧间 · 技术架构文档 v0.2

> 梁龙科技 | 更新：2026-09-04

## 1. 技术选型

| 层 | 选型 | 理由 |
|----|------|------|
| 前端 | React 18 + Vite + TypeScript | 生态最大、AI 编码支持强、未来招人容易（已拍板） |
| UI | Tailwind CSS（待装） | 快速出专业界面 |
| 后端 | Python FastAPI + SQLAlchemy(async) + asyncpg | 数据科学背景顺手；async 适合爬虫与并发调模型 |
| 数据库 | PostgreSQL 16（本机 Homebrew） | v1 延续；`frames_dev` 已建 |
| AI 推理 | 云端 API 网关（DeepSeek 起步） | 本地 16GB 不跑大模型；API-key 由用户自带 |
| 形态 | Web/PWA（浏览器访问本地后端） | 已拍板；未来 Tauri 包壳 |

## 2. 仓库结构

```
~/Projects/frames/
├── .gitignore
├── docs/                    # 项目文档（断点续传入口）
│   ├── 00-README.md         # 首页导航
│   ├── 01-product-design.md # 产品设计
│   ├── 02-technical-architecture.md  # 本文档
│   ├── 03-development-log.md         # 开发日志
│   └── 04-data-model.md     # 数据模型
├── backend/
│   ├── .venv/               # 虚拟环境（不入库）
│   ├── requirements.txt
│   └── app/
│       └── main.py          # FastAPI 入口（当前最小骨架）
└── frontend/                # Vite react-ts 脚手架
```

## 3. 本地启动

前置：PATH 含 `/opt/homebrew/bin`（已写入 `~/.zshrc`）。

```bash
# 后端（端口 8000）
cd ~/Projects/frames/backend
.venv/bin/uvicorn app.main:app --port 8000

# 前端（端口 5173）
cd ~/Projects/frames/frontend
npm run dev
```

健康检查：`curl http://127.0.0.1:8000/api/health/db` → `{"database":"ok"}`

## 4. 配置管理

- 数据库连接：环境变量 `DATABASE_URL`，默认 `postgresql+asyncpg://zhuolittlelong@localhost:5432/frames_dev`
- 后续敏感项（API-key 等）一律走 `.env`（不入库），见 `.gitignore`
- Python 依赖锁定于 `requirements.txt`；变更后 `pip freeze > requirements.txt` 更新

## 5. 关键设计约定（持续补充）

### 5.1 模型网关（H 模块雏形）
- 统一调用层，屏蔽 Provider 差异（DeepSeek 起步，预留 qwen-vl 视觉等）
- 按任务类型路由（拆解各层/创作/变异建议…）
- API-key 存本地配置，由用户自带
- 设计目标：替换 Provider 不改业务代码

### 5.2 拆解与生成解耦（v1 经验）
- 拆解（分析资产、可复用沉淀）与创作（带需求的生成）独立，不合成一条 pipeline
- 首页只负责拆解；生成入口分散在拆解结果页/参考片/需求详情

### 5.3 媒体即用即清
- 拆解过程产生的密集截图/临时媒体仅在任务期保留，完成后清理，控制存储

### 5.4 数据访问层抽象
- v2 本地优先（单用户+自带 key），但 SQLAlchemy 模型与 repository 层分离，为未来团队云同步预留

## 6. 决策记录（ADR）

| 日期 | 决策 | 理由 |
|------|------|------|
| 2026-09-04 | 前端 React+Vite+TS | 用户拍板；生态/招人/AI 支持 |
| 2026-09-04 | 产品形态 Web/PWA（非原生 App） | 单人最快落地、五端可访问、可平滑包壳 |
| 2026-09-04 | 后端 FastAPI+asyncpg | v1 Python 延续 + async 优势 |
| 2026-09-04 | 数据库沿用 PostgreSQL | v1 选型延续，本机 16 已运行 |
