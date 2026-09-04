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

> 断点续传第一入口：先读 [03-development-log.md](03-development-log.md) 的最新记录，确认当前进度与下一步。

## 快速启动（当前骨架阶段）

前置：macOS 本机已装 Homebrew（`/opt/homebrew`），PostgreSQL 16 运行中，Node ≥ 20。

```bash
# 后端
cd ~/Projects/frames/backend
python3 -m venv .venv            # 首次
.venv/bin/pip install -r requirements.txt   # 首次
.venv/bin/uvicorn app.main:app --port 8000  # 启动

# 前端（另开终端）
cd ~/Projects/frames/frontend
npm install                      # 首次
npm run dev                      # 启动，默认 http://localhost:5173
```

## 状态徽标

- 阶段：骨架搭建 ✅ → 数据模型设计（进行中）
- 详细进度见 `03-development-log.md`
