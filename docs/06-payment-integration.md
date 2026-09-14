---
AIGC:
    Label: "1"
    ContentProducer: 001191440300708461136T1XGW3
    ProduceID: ce0d790a7b004233c1097246b343160b_acbf1d8eab9911f18f50525400aeaaa3
    ReservedCode1: 7PRKmXZuhl5M+IcLWDphNdhTbtwRsYjtU2ER13CbZ+tc/4x5wwAT+atzVH6UFx076McDJrNiH+PtJIjo815oeBOGyagYtHffIUw7hgDVJSRBwLXjQxhldlODr++Zqsjz06gEutzRl4ruhuVTKET66sy3Cz/8SE3gk2G+pZn23VG6FjlNia4hWfLgjic=
    ContentPropagator: 001191440300708461136T1XGW3
    PropagateID: ce0d790a7b004233c1097246b343160b_acbf1d8eab9911f18f50525400aeaaa3
    ReservedCode2: 7PRKmXZuhl5M+IcLWDphNdhTbtwRsYjtU2ER13CbZ+tc/4x5wwAT+atzVH6UFx076McDJrNiH+PtJIjo815oeBOGyagYtHffIUw7hgDVJSRBwLXjQxhldlODr++Zqsjz06gEutzRl4ruhuVTKET66sy3Cz/8SE3gk2G+pZn23VG6FjlNia4hWfLgjic=
---

# 帧间 · 真实支付接入方案（v0.1 草案）

> 状态：待落地（2026-09-08）
> 背景：docs/05-points-billing.md v0.2 已落地「模拟充值」。本文回答两个问题：
> 1. 真实充值后钱到哪：进入「你自己的商户收款账户」（微信支付商户号 / 支付宝商家账户），系统回调后给用户加点数。
> 2. 通道怎么选、什么时候能上：见下文 1–4。

---

## 1. 现状与差距

| 环节 | 模拟（当前） | 真实（目标） |
|---|---|---|
| 用户点套餐 | 后端直接 +点数 | 后端生成支付订单 → 拉起微信/支付宝 |
| 钱 | 不发生 | 进你的商户账户（T+1 结算到你的银行卡） |
| 到账判定 | 点击即到账 | 支付平台回调 `paid` 后才加点数 |
| 防作弊 | 无 | 金额签名校验、订单幂等、回调验签 |

数据层已具备扩展位：`credit_transactions.provider / trade_no` 已预留；
`credit_accounts.total_recharged_points` 等字段无需改动。

## 2. 通道选型（单人 SaaS 场景）

| 通道 | 门槛 | 费率 | 适合度 | 备注 |
|---|---|---|---|---|
| 微信 Native 支付 | 需企业/个体户执照 + 微信商户号 | 0.6%（优惠期 0.38%） | ★★★ 主力 | 扫码支付，无需 App，回调最稳；需已备案域名 |
| 支付宝「当面付/电脑网站」 | 需企业/个体户执照 + 支付宝商家账户 | 0.6% | ★★ 备选 | 电脑网站版更贴当前 Web 产品 |
| 聚合第三方（虎皮椒 / PayJS 等） | 个人可申请 | 1.2%–2%（偏高） | ★ 过渡 | 未注册主体时的"绕行方案"，合规与稳定性一般 |
| 微信/支付宝当面收款码（个人码） | 个人即可 | 0 | ✗ 不可用 | 无法自动回调，只能人工确认，不做 |

**建议路线**：
- 阶段 A（未注册主体，验证需求）：继续模拟充值，不接真实支付；把「支付后加点」预留好。
- 阶段 B（有真实付费意愿用户、注册个体工商户后）：优先微信 Native + 支付宝电脑网站双通道。
- 阶段 C（流水稳定后）：注册公司主体，费率/额度/退款更优。

## 3. 落地前置条件清单（按依赖顺序）

| # | 条件 | 耗时 | 说明 |
|---|---|---|---|
| 1 | 注册个体工商户（或公司） | 各地 3–10 个工作日 | 梁龙科技目前未注册，这是最大前置阻塞 |
| 2 | 域名 + ICP 备案 | 备案 7–20 天 | 微信/支付宝回调要求已备案域名 + HTTPS；本地 127.0.0.1 只能联调沙箱 |
| 3 | 申请微信商户号（mchid） | 资料齐全 1–3 天 | 需营业执照、法人身份证、结算银行卡、对公/法人账户 |
| 4 | 申请支付宝商家账户 | 1–3 天 | 同上 |
| 5 | 后端支付模块开发 | 1–2 天 | 见 §4 |
| 6 | 前端充值改造成拉起支付 | 0.5–1 天 | 见 §4 |
| 7 | 沙箱联调 + 真实 1 元测试 | 0.5 天 | 回调验签、幂等、到账加点 |

**最短现实路径（乐观估算）**：注册个体户（最快 ~1 周）+ 备案（可并行）→ 商户号申请（~3 天）→ 技术接入（~2 天）
≈ **2–4 周** 可上线真实支付。若全程被资料/审核卡住，需 1–2 个月。

## 4. 后端与前端改动设计（供开发时照做）

### 4.1 后端（新增 routers/payments.py，prefix /api/payments）
1. `POST /api/payments/prepay`：入参 `{package}` → 校验套餐 → 幂等查询未支付订单 → 调微信 Native 下单 API 生成 `code_url`（二维码内容）/ 或支付宝生成支付链接 → 落 `payment_orders` 表（status=pending）。
2. `payment_orders` 表（新建）：
   | 字段 | 说明 |
   |---|---|
   | id | UUID 主键 |
   | account_id | 关联 credit_accounts |
   | package_key | light/creator/team |
   | amount_cny | 金额（分存储或 decimal） |
   | provider | wechat/alipay |
   | trade_no | 平台单号（下单返回） |
   | status | pending/paid/closed/refunded |
   | paid_at | 支付成功时间 |
3. `POST /api/payments/notify/{provider}`：支付平台回调端点——验签 → 按 trade_no 查订单 → 若已 paid 直接返回成功（幂等）→ 置 paid → 调 `billing.recharge_after_paid(db, order)` 给账户加点 → 返回「成功」给平台。
   > 关键：**只有回调成功才加点数**；前端不能信自己的按钮。
4. 加一个「模拟支付」端点仅限 DEBUG 环境（env=dev 才注册），方便本地不接平台时联调回调。

### 4.2 前端（BillingPanel 改造）
1. 点套餐 → `POST prepay` → 若返回 `mock_paid=true`（dev）直接刷新余额；否则展示二维码弹窗（微信扫码）或跳转链接（支付宝）。
2. 弹窗内轮询 `GET /api/payments/order/{trade_no}`，status=paid → 关闭弹窗 → 刷新点数账户 → 提示到账。
3. 未支付/已关闭订单可重新发起，旧订单自动 close。

### 4.3 安全要点
- 回调验签：微信用平台证书（Wechatpay-Signature）/ 支付宝用 RSA2 验签，**禁止裸信回调**。
- 幂等：同一 trade_no 重复回调不重复加点。
- 防篡改：prepay 只收 package_key，金额一律后端查套餐表，不信任前端传价。
- 日志：payment_orders 全程留痕，退款另走平台商户后台或退款接口。

## 5. 何时建议启动

建议**不要在纯开发阶段**申请商户号（审核占用 + 无意义）。
触发条件（满足其一即启动）：
- 有 5+ 个真实用户主动问"怎么付费 / 想开通"；
- 你准备把帧间开放给外部编导试用并收体验费；
- 你已完成个体工商户注册。

> 在此之前的每一笔"充值"都继续走模拟，点数逻辑与正式版完全一致，切换时无需改业务扣点代码。
*（内容由AI生成，仅供参考）*
