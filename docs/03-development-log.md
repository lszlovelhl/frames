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

### 断点重启步骤（任何时候回来从这里开始）

```bash
# 1. 启动 PostgreSQL（若未运行）
brew services start postgresql@16

# 2. 启动后端
cd ~/Projects/frames/backend && .venv/bin/uvicorn app.main:app --port 8000

# 3. 启动前端（另开终端）
cd ~/Projects/frames/frontend && npm run dev

# 4. 验证
curl http://127.0.0.1:8000/api/health/db
# 浏览器打开 http://localhost:5173
```

### 下一步（待办）

- [ ] **推送远程仓库**（GitHub 账号 lszlovelhl 已存在）：建议 `gh repo create` 或网页建仓后 push，防止再次丢失
- [ ] 设计核心数据模型（docs/04-data-model.md）：拆解记录/元素/标注/提示词/创作结果等表
- [ ] 申请/配置 DeepSeek API Key，搭最小模型网关调用
- [ ] 前端装 Tailwind，搭基础布局（侧边栏+主区）
- [ ] 回填蓝图 v0.1 中待补全项（v1 表结构、提示词资产细节）

### 关键路径备忘

- 项目根：`~/Projects/frames`
- 蓝图文档（会话产出）：`~/Library/.../workspace/conv_*/output/梁龙科技-帧间重建蓝图-v0.1.md`（核心方法论已并入 docs/01）
- 连接串默认：`postgresql+asyncpg://zhuolittlelong@localhost:5432/frames_dev`
