import { useCallback, useEffect, useRef, useState } from "react";
import {
  api,
  type AiProviderCard,
  type ProviderUsageDetail,
  type ProviderBalanceStatus,
  type AiUsageRecentItem,
} from "../api";
import BillingPanel from "./BillingPanel";

const SCENE_LABEL: Record<string, string> = {
  breakdown: "五层拆解",
  vision: "视觉理解",
  creation_draft: "创作台 · 初稿",
  creation_edit: "创作台 · 续改",
  element_mix: "元素变异/组合",
  misc: "其他",
};

function fmtTime(iso: string | null): string {
  if (!iso) return "--";
  const d = new Date(iso);
  const p = (n: number) => String(n).padStart(2, "0");
  return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

function fmtTokens(n: number | null | undefined): string {
  if (n == null) return "--";
  return Number(n).toLocaleString();
}

function fmtCost(n: number | null | undefined): string {
  if (n == null) return "--";
  return `¥${Number(n).toFixed(2)}`;
}

const STATUS_META: Record<ProviderBalanceStatus, { label: string; cls: string; dot: string }> = {
  ok: { label: "余额正常", cls: "text-emerald-300", dot: "bg-emerald-400" },
  low: { label: "余额不足", cls: "text-amber-300", dot: "bg-amber-400" },
  unknown: { label: "余额未知", cls: "text-zinc-400", dot: "bg-zinc-500" },
  no_key: { label: "未填 api-key", cls: "text-zinc-500", dot: "bg-zinc-600" },
};

/* ---------------- 余额徽标 ---------------- */
function BalanceBadge({ card }: { card: AiProviderCard }) {
  const m = STATUS_META[card.balance_status];
  if (card.balance_status === "no_key") {
    return (
      <span className="inline-flex items-center gap-1.5 rounded-full bg-white/5 px-2 py-0.5 text-[10px] text-zinc-400">
        <span className="h-1.5 w-1.5 rounded-full bg-zinc-500" />
        未填 api-key
      </span>
    );
  }
  if (card.balance_cny == null) {
    return (
      <span className="inline-flex items-center gap-1.5 rounded-full bg-white/5 px-2 py-0.5 text-[10px] text-zinc-400">
        <span className={`h-1.5 w-1.5 rounded-full ${m.dot}`} />
        余额未知
      </span>
    );
  }
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-[10px] ${m.cls}`}>
      <span className={`h-1.5 w-1.5 rounded-full ${m.dot}`} />
      {card.balance_manual ? "手动" : ""}余额 ¥{card.balance_cny.toFixed(2)}
    </span>
  );
}

/* ---------------- Provider 卡片 ---------------- */
function ProviderCard({
  card,
  onOpen,
}: {
  card: AiProviderCard;
  onOpen: () => void;
}) {
  const low = card.balance_status === "low";
  return (
    <div className="flex flex-col rounded-xl border border-white/5 bg-[#1c1f26] p-4">
      <div className="flex items-start justify-between gap-2">
        <div className="flex min-w-0 items-center gap-2">
          {card.logo_url ? (
            <img src={card.logo_url} alt="" className="h-5 w-5 rounded" />
          ) : (
            <span
              className="flex h-5 w-5 items-center justify-center rounded text-[10px] font-bold text-black"
              style={{ background: card.brand_color || "#888" }}
            >
              {card.name.slice(0, 1)}
            </span>
          )}
          <div className="min-w-0">
            <div className="truncate text-sm font-semibold text-zinc-100">{card.name}</div>
            <div className="truncate font-mono text-[10px] text-zinc-500">{card.api_key_masked}</div>
          </div>
        </div>
        <BalanceBadge card={card} />
      </div>

      {card.warn && (
        <div className="mt-2 rounded-lg border border-amber-400/20 bg-amber-400/10 px-2 py-1.5 text-[11px] leading-snug text-amber-200">
          {card.warn}
        </div>
      )}

      <div className="mt-3 grid grid-cols-3 gap-2 text-center">
        <div className="rounded-lg bg-white/[0.04] px-1 py-2">
          <div className="text-lg font-semibold text-zinc-100">{card.today?.calls ?? 0}</div>
          <div className="text-[10px] text-zinc-500">今日调用</div>
        </div>
        <div className="rounded-lg bg-white/[0.04] px-1 py-2">
          <div className="text-lg font-semibold text-amber-200">{fmtCost(card.today?.cost_cny ?? 0)}</div>
          <div className="text-[10px] text-zinc-500">今日成本</div>
        </div>
        <div className="rounded-lg bg-white/[0.04] px-1 py-2">
          <div className="text-lg font-semibold text-zinc-100">{card.usage?.calls ?? 0}</div>
          <div className="text-[10px] text-zinc-500">累计调用</div>
        </div>
      </div>

      <div className="mt-2 flex flex-wrap gap-1">
        {card.models.slice(0, 4).map((m) => (
          <span key={m.id} className="rounded bg-white/5 px-1.5 py-0.5 font-mono text-[9px] text-zinc-400">
            {m.id}
          </span>
        ))}
        {card.models.length > 4 && (
          <span className="rounded bg-white/5 px-1.5 py-0.5 text-[9px] text-zinc-500">+{card.models.length - 4}</span>
        )}
      </div>

      <div className="mt-3 flex items-center gap-2">
        <button onClick={onOpen} className="flex-1 rounded-lg border border-white/10 px-2 py-1.5 text-xs text-zinc-300 hover:bg-white/5">
          查看用量详情
        </button>
        {low && card.topup_url && (
          <a
            href={card.topup_url}
            target="_blank"
            rel="noreferrer"
            className="rounded-lg bg-amber-300 px-2 py-1.5 text-xs font-medium text-black hover:bg-amber-200"
          >
            去充值
          </a>
        )}
      </div>
    </div>
  );
}

/* ---------------- 空态：去模型管理接入 ---------------- */
function EmptyHint({ hasTemplates }: { hasTemplates: boolean }) {
  return (
    <div className="rounded-xl border border-dashed border-white/10 p-10 text-center">
      <p className="text-sm text-zinc-400">还没有可用的 AI 服务商</p>
      <p className="mt-1 text-xs text-zinc-600">
        {hasTemplates
          ? "系统已预置 DeepSeek / 豆包 / Moonshot 等模型接口模板，填好 api-key 即可使用"
          : "请到「模型管理」页接入服务商并填写 api-key"}
      </p>
    </div>
  );
}

/* ---------------- 服务商用量详情（点卡片后展示） ---------------- */
function ProviderDetail({ providerKey, onBack }: { providerKey: string; onBack: () => void }) {
  const [data, setData] = useState<ProviderUsageDetail | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      setData(await api.aiProviderUsage(providerKey));
      setError("");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [providerKey]);

  useEffect(() => {
    void load();
  }, [load]);

  async function refreshBalance() {
    setBusy(true);
    try {
      await api.aiProviderRefreshBalance(providerKey);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  const card = data?.provider;
  const low = card?.balance_status === "low";
  return (
    <div className="rounded-xl border border-white/5 bg-[#1c1f26] p-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <button onClick={onBack} className="text-[11px] text-zinc-500 hover:text-zinc-300">
            ← 返回全部服务商
          </button>
          <div className="mt-1 flex items-center gap-2">
            <h2 className="text-base font-semibold text-zinc-100">{card?.name ?? providerKey}</h2>
            {card && <BalanceBadge card={card} />}
          </div>
          {card?.balance_checked_at && (
            <div className="mt-0.5 text-[10px] text-zinc-600">
              余额查询于 {fmtTime(card.balance_checked_at)}
              {card.balance_manual ? " · 手动维护，未走接口" : ""}
            </div>
          )}
        </div>
        <div className="flex items-center gap-2">
          <button onClick={() => void refreshBalance()} disabled={busy} className="rounded-lg border border-white/10 px-3 py-1.5 text-xs text-zinc-300 hover:bg-white/5 disabled:opacity-50">
            {busy ? "查询中…" : "刷新余额"}
          </button>
          {card?.topup_url && (
            <a href={card.topup_url} target="_blank" rel="noreferrer" className="rounded-lg bg-amber-300 px-3 py-1.5 text-xs font-medium text-black hover:bg-amber-200">
              充值页 ↗
            </a>
          )}
        </div>
      </div>

      {error && <p className="mt-3 rounded-lg border border-rose-400/20 bg-rose-400/10 px-3 py-2 text-xs text-rose-300">{error}</p>}
      {low && card?.warn && (
        <div className="mt-3 rounded-lg border border-amber-400/20 bg-amber-400/10 px-3 py-2 text-xs text-amber-200">
          {card.warn}，为避免拆解中断建议尽快充值。
        </div>
      )}

      {!data ? (
        <p className="mt-4 text-sm text-zinc-500">加载中…</p>
      ) : (
        <>
          <div className="mt-4 grid grid-cols-2 gap-3 md:grid-cols-4">
            {[
              ["今日调用", data.today.calls.toLocaleString(), data.today.cost_cny],
              ["今日成本", `¥${data.today.cost_cny.toFixed(2)}`, null],
              ["累计调用", data.total.calls.toLocaleString(), data.total.cost_cny],
              ["累计成本", fmtCost(data.total.cost_cny), null],
            ].map(([label, value, sub]) => (
              <div key={label as string} className="rounded-lg bg-white/[0.04] p-3">
                <div className="text-[10px] uppercase tracking-wider text-zinc-500">{label}</div>
                <div className="mt-1 text-lg font-semibold text-zinc-100">{value}</div>
                {sub != null && <div className="text-[10px] text-zinc-500">¥{Number(sub).toFixed(4)}</div>}
              </div>
            ))}
          </div>

          {data.by_model.length > 0 && (
            <div className="mt-4">
              <div className="mb-2 text-[10px] uppercase tracking-wider text-zinc-500">按模型拆分</div>
              <table className="w-full text-left text-xs">
                <thead>
                  <tr className="border-b border-white/5 text-[10px] uppercase tracking-wider text-zinc-500">
                    <th className="py-1 pr-2 font-medium">档位 / 模型</th>
                    <th className="py-1 pr-2 text-right font-medium">调用</th>
                    <th className="py-1 pr-2 text-right font-medium">总 Tokens</th>
                    <th className="py-1 text-right font-medium">成本</th>
                  </tr>
                </thead>
                <tbody>
                  {data.by_model.map((r) => (
                    <tr key={r.alias + r.model} className="border-b border-white/[0.03] last:border-0">
                      <td className="py-1.5 pr-2">
                        <span className="font-mono text-[10px] text-zinc-300">{r.alias}</span>
                        <span className="ml-2 font-mono text-[10px] text-zinc-500">{r.model}</span>
                      </td>
                      <td className="py-1.5 pr-2 text-right text-zinc-300">{r.calls.toLocaleString()}</td>
                      <td className="py-1.5 pr-2 text-right text-zinc-400">{fmtTokens(r.total_tokens)}</td>
                      <td className="py-1.5 text-right text-amber-200/90">{fmtCost(r.cost_cny)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          <div className="mt-4">
            <div className="mb-2 text-[10px] uppercase tracking-wider text-zinc-500">最近流水</div>
            {data.recent.length === 0 ? (
              <p className="text-xs text-zinc-600">暂无调用记录</p>
            ) : (
              <div className="max-h-[300px] overflow-y-auto">
                <table className="w-full text-left text-xs">
                  <thead className="sticky top-0 bg-[#1c1f26]">
                    <tr className="border-b border-white/5 text-[10px] uppercase tracking-wider text-zinc-500">
                      <th className="py-1.5 pr-2 font-medium">时间</th>
                      <th className="py-1.5 pr-2 font-medium">场景</th>
                      <th className="py-1.5 pr-2 font-medium">档位</th>
                      <th className="py-1.5 pr-2 text-right font-medium">Tokens</th>
                      <th className="py-1.5 text-right font-medium">成本</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.recent.map((r) => (
                      <tr key={r.id} className="border-b border-white/[0.03] last:border-0 text-zinc-400" title={r.error || undefined}>
                        <td className="whitespace-nowrap py-1.5 pr-2 font-mono text-[10px] text-zinc-500">{fmtTime(r.created_at)}</td>
                        <td className="py-1.5 pr-2">{SCENE_LABEL[r.scene ?? ""] ?? r.scene ?? "—"}</td>
                        <td className="py-1.5 pr-2 font-mono text-[10px]">{r.alias}</td>
                        <td className="py-1.5 pr-2 text-right font-mono text-[10px]">{fmtTokens(r.total_tokens)}</td>
                        <td className="py-1.5 text-right text-amber-200/90">{fmtCost(r.cost_cny)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}

/* ---------------- 主视图 ---------------- */
export default function UsageView() {
  const [cards, setCards] = useState<AiProviderCard[]>([]);
  const [templates, setTemplates] = useState<AiProviderCard[]>([]);
  const [openKey, setOpenKey] = useState<string | null>(null);
  const [recent, setRecent] = useState<AiUsageRecentItem[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [auto, setAuto] = useState(true);
  const [pulse, setPulse] = useState(0);
  const lastRef = useRef<string | null>(null);

  const loadCards = useCallback(async () => {
    try {
      const data = await api.aiProviders();
      setCards(data.items ?? []);
      setTemplates(data.templates ?? []);
      setError("");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  const loadRecent = useCallback(async () => {
    try {
      const data = await api.aiUsageRecent(30);
      const items = data.items ?? [];
      setRecent(items);
      if (items.length > 0) lastRef.current = items[0].id;
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    void (async () => {
      setLoading(true);
      await Promise.all([loadCards(), loadRecent()]);
      setLoading(false);
    })();
  }, [loadCards, loadRecent]);

  useEffect(() => {
    if (!auto) return;
    const t = setInterval(() => {
      void Promise.all([loadCards(), loadRecent()]).then(() => setPulse((p) => p + 1));
    }, 30000);
    return () => clearInterval(t);
  }, [auto, loadCards, loadRecent]);

  const totalCalls = cards.reduce((s, c) => s + (c.usage?.calls ?? 0), 0);
  const totalCost = cards.reduce((s, c) => s + (c.usage?.cost_cny ?? 0), 0);
  const todayCalls = cards.reduce((s, c) => s + (c.today?.calls ?? 0), 0);
  const todayCost = cards.reduce((s, c) => s + (c.today?.cost_cny ?? 0), 0);
  const lowCards = cards.filter((c) => c.balance_status === "low");

  return (
    <div className="mx-auto max-w-6xl px-4 py-6 lg:px-8">
      <header className="mb-5 flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-zinc-100">AI 用量与计费</h1>
          <p className="mt-1 text-sm text-zinc-500">
            按「已填 api-key 的服务商」动态展示 · 余额每 30 分钟自动核对 · 成本按 token 单价估算
          </p>
        </div>
        <div className="flex items-center gap-3">
          <label className="flex cursor-pointer items-center gap-2 text-xs text-zinc-400">
            <button
              onClick={() => setAuto((a) => !a)}
              className={`relative h-5 w-9 rounded-full transition ${auto ? "bg-amber-300/80" : "bg-white/10"}`}
              title="开关自动刷新"
            >
              <span className={`absolute top-0.5 h-4 w-4 rounded-full bg-white shadow transition-all ${auto ? "left-[18px]" : "left-0.5"}`} />
            </button>
            自动刷新{auto && <span className="ml-0.5 animate-pulse text-emerald-300">●</span>}
          </label>
          <button
            onClick={() => void Promise.all([loadCards(), loadRecent()])}
            className="rounded-lg border border-white/10 px-3 py-1.5 text-xs text-zinc-400 hover:bg-white/5"
          >
            刷新
          </button>
        </div>
      </header>

      {/* 点数计费账户 */}
      <BillingPanel />

      {error && <p className="mb-4 rounded-lg border border-rose-400/20 bg-rose-400/10 px-3 py-2 text-sm text-rose-300">{error}</p>}
      {loading && <p className="text-sm text-zinc-500">加载中…</p>}

      {/* 余额不足预警 */}
      {lowCards.length > 0 && (
        <div className="mb-4 space-y-2">
          {lowCards.map((c) => (
            <div key={c.key} className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-amber-400/25 bg-amber-400/10 px-4 py-3">
              <div className="text-sm text-amber-200">
                {c.warn ?? `${c.name} 余额不足`}
                {c.today?.calls ? ` · 今日已调用 ${c.today.calls} 次` : ""}
              </div>
              {c.topup_url && (
                <a href={c.topup_url} target="_blank" rel="noreferrer" className="rounded-lg bg-amber-300 px-3 py-1.5 text-xs font-medium text-black hover:bg-amber-200">
                  去充值 · {c.name}
                </a>
              )}
            </div>
          ))}
        </div>
      )}

      {/* 汇总条 */}
      {cards.length > 0 && (
        <div className="mb-4 grid gap-4 md:grid-cols-4">
          <div className="rounded-xl border border-white/5 bg-[#1c1f26] p-4">
            <div className="text-[11px] uppercase tracking-wider text-zinc-500">今日调用</div>
            <div className="mt-1 text-xl font-semibold text-zinc-100">{todayCalls.toLocaleString()}</div>
            <div className="text-[10px] text-zinc-500">成本 ¥{todayCost.toFixed(4)}</div>
          </div>
          <div className="rounded-xl border border-white/5 bg-[#1c1f26] p-4">
            <div className="text-[11px] uppercase tracking-wider text-zinc-500">累计调用</div>
            <div className="mt-1 text-xl font-semibold text-zinc-100">{totalCalls.toLocaleString()}</div>
            <div className="text-[10px] text-zinc-500">成本 ¥{totalCost.toFixed(4)}</div>
          </div>
          <div className="rounded-xl border border-white/5 bg-[#1c1f26] p-4">
            <div className="text-[11px] uppercase tracking-wider text-zinc-500">可用服务商</div>
            <div className="mt-1 text-xl font-semibold text-emerald-300">{cards.filter((c) => c.api_key_set).length}</div>
            <div className="text-[10px] text-zinc-500">共 {cards.length} 个配置</div>
          </div>
          <div className="rounded-xl border border-white/5 bg-[#1c1f26] p-4">
            <div className="text-[11px] uppercase tracking-wider text-zinc-500">余额告警</div>
            <div className={`mt-1 text-xl font-semibold ${lowCards.length > 0 ? "text-amber-300" : "text-zinc-100"}`}>{lowCards.length}</div>
            <div className="text-[10px] text-zinc-500">{lowCards.length > 0 ? "需关注充值" : "全部正常"}</div>
          </div>
        </div>
      )}

      {/* 服务商卡片集合 */}
      {openKey ? (
        <ProviderDetail providerKey={openKey} onBack={() => setOpenKey(null)} />
      ) : cards.filter((c) => c.api_key_set).length === 0 ? (
        <EmptyHint hasTemplates={templates.length > 0} />
      ) : (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {cards
            .filter((c) => c.api_key_set)
            .map((c) => (
              <ProviderCard key={c.key} card={c} onOpen={() => setOpenKey(c.key)} />
            ))}
        </div>
      )}

      {/* 已配置但未填 key 的服务商（待接入提示） */}
      {!openKey && cards.filter((c) => !c.api_key_set).length > 0 && (
        <div className="mt-4 rounded-xl border border-dashed border-white/10 p-4">
          <div className="text-[11px] uppercase tracking-wider text-zinc-600">待填 api-key（可到「模型管理」页补填后自动出现在上方）</div>
          <div className="mt-2 flex flex-wrap gap-2">
            {cards
              .filter((c) => !c.api_key_set)
              .map((c) => (
                <span key={c.key} className="rounded-lg bg-white/5 px-2 py-1 font-mono text-[10px] text-zinc-500">
                  {c.name} · {c.base_url}
                </span>
              ))}
          </div>
        </div>
      )}

      {/* 最近调用流水（全局，精简） */}
      {!openKey && recent.length > 0 && (
        <div className="mt-6 rounded-xl border border-white/5 bg-[#1c1f26] p-4">
          <div className="mb-2 flex items-center justify-between">
            <div className="flex items-center gap-2">
              <span className="h-3.5 w-1 rounded-full bg-amber-300/70" />
              <span className="text-xs font-medium uppercase tracking-wider text-zinc-500">最近调用流水</span>
            </div>
            <div className="flex items-center gap-3 text-[10px] text-zinc-500">
              {auto && <span className="text-emerald-300/90">30s 自动刷新{pulse > 0 ? ` · ${pulse} 次` : ""}</span>}
              <span>{recent.length} 条</span>
            </div>
          </div>
          <div className="max-h-[300px] overflow-y-auto">
            <table className="w-full text-left text-xs">
              <thead className="sticky top-0 bg-[#1c1f26]">
                <tr className="border-b border-white/5 text-[10px] uppercase tracking-wider text-zinc-500">
                  <th className="py-1.5 pr-2 font-medium">时间</th>
                  <th className="py-1.5 pr-2 font-medium">服务商</th>
                  <th className="py-1.5 pr-2 font-medium">场景</th>
                  <th className="py-1.5 pr-2 font-medium">档位</th>
                  <th className="py-1.5 pr-2 text-right font-medium">Tokens</th>
                  <th className="py-1.5 text-right font-medium">成本</th>
                </tr>
              </thead>
              <tbody>
                {recent.map((r) => (
                  <tr key={r.id} className="border-b border-white/[0.03] last:border-0 text-zinc-400" title={r.error || undefined}>
                    <td className="whitespace-nowrap py-1.5 pr-2 font-mono text-[10px] text-zinc-500">{fmtTime(r.created_at)}</td>
                    <td className="py-1.5 pr-2 text-zinc-300">{r.provider ?? "deepseek"}</td>
                    <td className="py-1.5 pr-2">{SCENE_LABEL[r.scene ?? ""] ?? r.scene ?? "—"}</td>
                    <td className="py-1.5 pr-2 font-mono text-[10px]">{r.alias}</td>
                    <td className="py-1.5 pr-2 text-right font-mono text-[10px]">{fmtTokens(r.total_tokens)}</td>
                    <td className="py-1.5 text-right text-amber-200/90">{fmtCost(r.cost_cny)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
