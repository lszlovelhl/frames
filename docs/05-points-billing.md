---
# 帧间 · 点数计费与防亏损设计 v0.3

> 状态：已落地（2026-09-08 计费闭环 v0.2；2026-09-10 增免计费开关 v0.3）
> 定位：AI 用量真实成本 → 点数定价 → 账户/余额/充值/扣点闭环 → 防亏损红线
> 配套：docs/02-technical-architecture.md（AI 网关） · docs/03-development-log.md · UsageView（含 BillingPanel）

---

## 1. 为什么按点数收费

帧间 v2 所有创作能力（拆解/视觉理解/创作指南/对话）都走自建 AI 网关，
每笔调用已落 `ai_usage_logs`（provider/alias/token/cost_cny）。
过去 5 天（9/4–9/8）真实扣费 30 元（非 DB 记账口径 12.76 元，DeepSeek 账单为准），
拆解 24 条视频 ≈ 14.5 元、vision 340 次 ≈ 9.6 元、指南 9 份 ≈ 3.9 元、对话/flash ≈ 1.7 元。

直接按人民币记账对用户体验不友好、退款/折扣/活动都不好做，故改为 **点数制**：
1 次动作 = 固定点数，套餐充值点数，调用扣点，余额不足拦截。

## 2. 单动作真实成本 → 定价

| 动作 | 真实成本（元/次） | 定价（成本×5 折算） | 扣点 |
|------|------|------|------|
| 短视频拆解（五层，含字幕+帧理解） | 1.0 | ≈5.0 元 | **100 点** |
| 长视频拆解（≥10 分钟） | 3.0 | ≈15.0 元 | **300 点** |
| 创作指南生成（8 章全量） | 1.5 | ≈7.5 元 | **150 点** |
| 对话（创作台/通用 chat，单轮） | 0.1 | ≈0.5 元 | **10 点** |

> 长/短视频阈值：默认 `duration_ms >= 10 * 60 * 1000` 视为长视频，可在配置调整。
> 视觉理解（vision）按帧单价已计入拆解成本，不单独面向用户扣点。

## 3. 套餐（已批准）

| 套餐 | 价格 | 点数 | 折合元/点 |
|------|------|------|-----------|
| 免费体验 | 0 元 | 100 点（一次，不可重复领取） | — |
| 轻量 | 29 元 | 700 点 | 0.0414 |
| 创作 | 79 元 | 2000 点 | 0.0395 |
| 团队 | 149 元 | 5000 点 | 0.0298 |

点数规则：**不过期**；月消费额度滚存 50%（按自然月结算，v0.1 仅落表与展示，结算任务后续阶段接定时器）。
重度用户（每日 ≈6 元真实成本）天然被点数套餐截断，套餐毛利可覆盖成本。

## 4. 防亏损红线（服务端强制执行）

1. **动作前预检**：调用前检查余额 ≥ 动作点数，不足返回 `402 INSUFFICIENT_POINTS`，不发起 AI 调用。
2. **成功后扣点**：AI 动作成功完成后落扣点流水；失败不扣（避免用户为失败买单）。
3. **日消费上限**：单账户每日扣点上限（默认 3000 点，配置可调），达上限当日拒绝新动作，防止被脚本打穿。
4. **免费体验限一次**：`free_claimed=true` 后不可重复领取。

### 4.1 免计费模式开关（v0.3，默认开启）

充值渠道上线前，上述 1/3 两条红线默认**不生效**（`precheck` 不校验余额与日上限），
创作台/拆解/对话不再因「点数不足」被 402 拦死；第 2 条语义改为「只记用量、不扣余额」。

| 项 | 说明 |
|---|---|
| 开关名 | `billing_enforced`（`false` = 免计费，**默认**；`true` = 强制计费） |
| 取值优先级 | 环境变量 `FRAMES_BILLING_ENFORCED`（`1/true/yes/on`）> `backend/data/runtime_config.json` > 默认 `false` |
| 生效方式 | 改 JSON **无需重启**（每次调用现读）；环境变量需重启/随进程注入，适合部署侧一键强制 |
| 免计费下流水 | `type="free_usage"`（点数记负、备注带「免计费模式，未扣点」），**不计入**「今日已用」与日上限 |
| 不受影响 | AI 真实调用与 `ai_usage_logs`（用量账）照常记录；动作价目表仍展示（仅作参考） |
| 切换回计费 | 充值渠道上线后：`runtime_config.json` 改 `"billing_enforced": true` 即可，业务代码零改动 |

> 覆盖范围：全部拦截点都收敛在 `services/billing.py` 的 `precheck()` / `consume()`，
> 因此该开关自动覆盖 videos（拆解/批量）、creations（对话/指南）、elements、products、ai 等所有调用方。

## 5. 数据模型

### credit_accounts（点数账户，1 用户 1 行）
| 字段 | 说明 |
|------|------|
| id | UUID |
| user_id | 关联 users（预留多用户；当前单机默认账户） |
| balance_points | 当前余额（点数） |
| total_recharged_points | 累计充值点数（不含赠送） |
| total_consumed_points | 累计消费点数 |
| free_claimed | 是否已领取免费体验 |
| created_at / updated_at | 时间戳 |

### credit_transactions（点数流水，充值/扣点/赠送统一记）
| 字段 | 说明 |
|------|------|
| id | UUID |
| account_id | 关联 credit_accounts |
| type | recharge（充值）/ consume（扣点）/ free_grant（免费赠送）/ refund / **free_usage（免计费模式下的用量流水，不扣余额）** |
| action | consume 时的动作名（breakdown_short/breakdown_long/guide_generate/chat） |
| points | 正负点数（recharge+ / consume-） |
| amount_cny | 充值金额（元，consume 为空） |
| ref_type / ref_id | 关联业务对象（analysis/creation…），便于追溯"哪个视频扣了" |
| note | 备注 |
| created_at | 时间戳 |

> 支付渠道：本地开发/演示无真实支付，充值为**模拟到账**（前端选择套餐 → 后端直接记 recharge 流水并加余额）。
> 预留 `provider`/`trade_no` 字段可在接入微信/支付宝时扩展，接口不变。

## 6. API（routers/billing.py）

| 接口 | 说明 |
|------|------|
| GET `/api/billing/account` | 当前账户：余额/累计充值/累计消费/免费领取状态/套餐列表/最近流水；**v0.3 增 `billing` 区块**（`{enforced, mode: free\|enforced, free_used_points, note}`） |
| POST `/api/billing/recharge` | 模拟充值：`{package: "light"/"creator"/"team"}` 或 `{points: n, amount_cny: n}` |
| POST `/api/billing/free-claim` | 领取免费体验 100 点（限一次） |
| GET `/api/billing/transactions` | 分页流水 |

余额不足时业务接口统一返回 `{"detail": {"code": "INSUFFICIENT_POINTS", "message": "点数不足，请充值", "required_points": n}}`。

## 7. 动作扣点接入点

| 业务接口 | 动作 | 点数 | 扣点时机 |
|------|------|------|------|
| POST /api/videos/{id}/analyse | breakdown_short / breakdown_long | 100 / 300 | 预检 → 拆解成功 → 扣点（续跑不重复扣） |
| POST /api/creations/guide/generate | guide_generate | 150 | 预检 → 生成成功 → 扣点 |
| POST /api/creations/chat、/api/creations/{id}/chat、/api/ai/chat | chat | 10 | 预检 → 调用成功 → 扣点 |
| POST /api/elements/mix（组合/变异） | chat | 10 | 预检 → AI 生成可用元素 → 扣点 |
| POST /api/products/autofill（AI 一键补全） | chat | 10 | 预检 → AI 补全成功 → 扣点（失败走兜底草稿不扣） |

> 指南 preview（速览确认）免费，避免用户为"看一眼方向"付费。

## 8. 落地记录（v0.2 · 2026-09-08）

- 后端：models 增 `credit_accounts` / `credit_transactions`；services/billing.py（precheck/consume/recharge/free_claim/account_payload）；routers/billing.py。
- 接入扣点：videos.py analyse、creations.py chat/continue_chat/guide_generate、ai.py chat、elements.py mix、products.py autofill；全部「预检 → 成功扣 → 失败不扣」。
- 前端：api.ts 增 billing* 方法并透出 402/429 结构化 detail；UsageView 顶部新增 BillingPanel（余额/动作价目/套餐模拟充值/免费领取/最近流水），AI 用量页一站式可见。
- 验证：account/recharge/free_claim/consume/日上限/余额不足 402 冒烟 PASS；BillingPanel 渲染与模拟充值（700 点到账）浏览器验证 PASS；element_mix / product_autofill 失败路径不扣点验证 PASS。
- 已知限制：DeepSeek 账户欠费（402），真实 AI 成功路径扣点需充值后再端到端复核；模拟充值待接真实支付渠道。

## 9. 前端展示

- UsageView 顶部加「点数账户」区块：余额点数、免费领取按钮、套餐卡片（模拟购买）、充值/消费流水精简列表。
- 余额不足时业务报错文案展示 + 引导前往充值。
- v0.3：账户区顶部徽标在免计费模式下显示「**免计费模式**」，并加一行「免计费期间已用 N 点（未扣余额）」；流水类型新增「免计费」标签。

## 10. 落地记录（v0.3 · 2026-09-10 免计费模式）

- 后端：`services/runtime_config.py` 增 `billing_enforced`（默认 false，env `FRAMES_BILLING_ENFORCED` > JSON > 默认）；`services/billing.py` 的 `precheck()` 免校验放行、`consume()` 记 `free_usage` 不扣余额、`account_payload()` 增 `billing` 区块。
- 覆盖拦截点：全部调用方（videos/creations/elements/products/ai）零代码改动自动受益。
- 前端：`api.ts` 增 `billing?` 类型；`BillingPanel.tsx` 增免计费徽标与免计费用量行；`dist` 已重建。
- 验收：真实链路（余额仅 5 点）——`guide/preview` 200、`guide/generate` 200 且**落库 8 章**、`chat` 200，余额 5 → 5 不变、`free_used_points` 累计 180；改 JSON 切 `true` 后 `chat` 立即 402 `INSUFFICIENT_POINTS`，切回 `false` 恢复 200；`ai_usage_logs` 照常（当日 49 次 / 149,577 tokens）。
- 遗留：免计费期间无余额与日上限约束，存在 AI 成本敞口；切回强制计费前需确保账户有余额。

*（内容由AI生成，仅供参考）*
