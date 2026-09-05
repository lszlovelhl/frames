import { useCallback, useEffect, useRef, useState } from "react";
import { api, type AiUsageAgg, type AiUsageRecentItem, type AiUsageSummary } from "../api";

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

function fmtTokens(n: number): string {
  return n.toLocaleString();
}

function fmtCost(n: number | null | undefined): string {
  if (n == null) return "--";
  return `¥${n.toFixed(4)}`;
}

function StatBlock({ title, sub, agg }: { title: string; sub: string; agg: AiUsageAgg | null }) {
  const avg = agg && agg.calls > 0 ? agg.cost_cny / agg.calls : 0;
  return (
    <div className="rounded-xl border border-white/5 bg-[#1c1f26] p-4">
      <div className="text-[11px] uppercase tracking-wider text-zinc-500">{title}</div>
      <div className="mt-0.5 text-[11px] text-zinc-600">{sub}</div>
      <div className="mt-3 grid grid-cols-4 gap-2">
        <div>
          <div className="text-xl font-semibold text-zinc-100">{agg ? agg.calls.toLocaleString() : "--"}</div>
          <div className="text-[10px] text-zinc-500">调用次数</div>
        </div>
        <div>
          <div className="text-xl font-semibold text-zinc-100">{agg ? fmtTokens(agg.total_tokens) : "--"}</div>
          <div className="text-[10px] text-zinc-500">总 Token</div>
        </div>
        <div>
          <div className="text-xl font-semibold text-amber-200">{agg ? fmtCost(agg.cost_cny) : "--"}</div>
          <div className="text-[10px] text-zinc-500">估算成本</div>
        </div>
        <div>
          <div className="text-xl font-semibold text-zinc-400">{agg ? fmtCost(avg) : "--"}</div>
          <div className="text-[10px] text-zinc-500">均次成本</div>
        </div>
      </div>
    </div>
  );
}

export default function UsageView() {
  const [summary, setSummary] = useState<AiUsageSummary | null>(null);
  const [recent, setRecent] = useState<AiUsageRecentItem[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [auto, setAuto] = useState(true);
  const [pulse, setPulse] = useState(0);
  const lastRef = useRef<string | null>(null);

  const loadSummary = useCallback(async () => {
    try {
      const data = await api.aiUsageSummary();
      setSummary(data);
      setError("");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  const loadRecent = useCallback(async () => {
    try {
      const data = await api.aiUsageRecent(40);
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
      await Promise.all([loadSummary(), loadRecent()]);
      setLoading(false);
    })();
  }, [loadSummary, loadRecent]);

  // 实时监测：开启后每 5 秒轮询最近流水
  useEffect(() => {
    if (!auto) return;
    const t = setInterval(() => {
      void loadRecent().then(() => setPulse((p) => p + 1));
    }, 5000);
    return () => clearInterval(t);
  }, [auto, loadRecent]);

  const total = summary?.total ?? null;
  const today = summary?.today ?? null;

  return (
    <div className="mx-auto max-w-5xl px-4 py-6 lg:px-8">
      <header className="mb-5 flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-zinc-100">AI 用量与计费</h1>
          <p className="mt-1 text-sm text-zinc-500">每轮模型调用自动落账 · 成本按 token 单价估算</p>
        </div>
        <div className="flex items-center gap-3">
          <label className="flex cursor-pointer items-center gap-2 text-xs text-zinc-400">
            <button
              onClick={() => setAuto((a) => !a)}
              className={`relative h-5 w-9 rounded-full transition ${auto ? "bg-amber-300/80" : "bg-white/10"}`}
              title="开关实时监测"
            >
              <span className={`absolute top-0.5 h-4 w-4 rounded-full bg-white shadow transition-all ${auto ? "left-[18px]" : "left-0.5"}`} />
            </button>
            实时监测{auto && <span className="ml-0.5 animate-pulse text-emerald-300">●</span>}
          </label>
          <button onClick={() => void Promise.all([loadSummary(), loadRecent()])} className="rounded-lg border border-white/10 px-3 py-1.5 text-xs text-zinc-400 hover:bg-white/5">
            刷新
          </button>
        </div>
      </header>

      {error && <p className="mb-4 rounded-lg border border-rose-400/20 bg-rose-400/10 px-3 py-2 text-sm text-rose-300">{error}</p>}
      {loading && <p className="text-sm text-zinc-500">加载中…</p>}

      {/* 汇总 */}
      <div className="mb-4 grid gap-4 md:grid-cols-2">
        <StatBlock title="今日用量" sub="自然日（UTC+8）" agg={today} />
        <StatBlock title="累计用量" sub="自 AI 记账启用以来" agg={total} />
      </div>

      {/* 按模型 */}
      <div className="mb-4 rounded-xl border border-white/5 bg-[#1c1f26] p-4">
        <div className="mb-3 text-xs font-medium uppercase tracking-wider text-zinc-500">按模型 / 档位拆分</div>
        {!summary || summary.by_alias.length === 0 ? (
          <p className="text-sm text-zinc-500">暂无数据</p>
        ) : (
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="border-b border-white/5 text-[11px] uppercase tracking-wider text-zinc-500">
                <th className="pb-2 pr-2 font-medium">模型</th>
                <th className="pb-2 pr-2 text-right font-medium">调用</th>
                <th className="pb-2 pr-2 text-right font-medium">Prompt Tokens</th>
                <th className="pb-2 pr-2 text-right font-medium">生成 Tokens</th>
                <th className="pb-2 pr-2 text-right font-medium">总 Tokens</th>
                <th className="pb-2 text-right font-medium">成本</th>
              </tr>
            </thead>
            <tbody>
              {summary.by_alias.map((row) => (
                <tr key={row.alias} className="border-b border-white/[0.03] last:border-0">
                  <td className="py-2 pr-2 font-mono text-xs text-zinc-300">{row.alias}</td>
                  <td className="py-2 pr-2 text-right text-zinc-300">{row.calls.toLocaleString()}</td>
                  <td className="py-2 pr-2 text-right text-zinc-500">{fmtTokens(row.prompt_tokens)}</td>
                  <td className="py-2 pr-2 text-right text-zinc-500">{fmtTokens(row.completion_tokens)}</td>
                  <td className="py-2 pr-2 text-right text-zinc-300">{fmtTokens(row.total_tokens)}</td>
                  <td className="py-2 text-right text-amber-200">{fmtCost(row.cost_cny)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* 实时流水 */}
      <div className="rounded-xl border border-white/5 bg-[#1c1f26] p-4">
        <div className="mb-3 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <span className="h-3.5 w-1 rounded-full bg-amber-300/70" />
            <span className="text-xs font-medium uppercase tracking-wider text-zinc-500">最近调用流水</span>
          </div>
          <div className="flex items-center gap-3 text-[10px] text-zinc-500">
            {auto && (
              <span className="text-emerald-300/90">
                每 5s 自动刷新{pulse > 0 ? ` · 已更新 ${pulse} 次` : ""}
              </span>
            )}
            <span>{recent.length} 条</span>
          </div>
        </div>
        {recent.length === 0 ? (
          <p className="py-6 text-center text-sm text-zinc-500">暂无调用记录，去跑一次拆解 / 创作后再回来看</p>
        ) : (
          <div className="max-h-[420px] overflow-y-auto">
            <table className="w-full text-left text-xs">
              <thead className="sticky top-0 bg-[#1c1f26]">
                <tr className="border-b border-white/5 text-[10px] uppercase tracking-wider text-zinc-500">
                  <th className="py-1.5 pr-2 font-medium">时间</th>
                  <th className="py-1.5 pr-2 font-medium">场景</th>
                  <th className="py-1.5 pr-2 font-medium">模型</th>
                  <th className="py-1.5 pr-2 text-right font-medium">Tokens</th>
                  <th className="py-1.5 pr-2 text-right font-medium">成本</th>
                  <th className="py-1.5 text-right font-medium">状态</th>
                </tr>
              </thead>
              <tbody>
                {recent.map((r) => (
                  <tr
                    key={r.id}
                    className={`border-b border-white/[0.03] last:border-0 ${r.id === lastRef.current ? "text-zinc-200" : "text-zinc-400"}`}
                    title={r.error || `${r.model ?? r.alias} · ${r.ref_type ?? ""} ${r.ref_id ?? ""}`}
                  >
                    <td className="whitespace-nowrap py-1.5 pr-2 font-mono text-[10px] text-zinc-500">{fmtTime(r.created_at)}</td>
                    <td className="py-1.5 pr-2">{SCENE_LABEL[r.scene ?? ""] ?? r.scene ?? "—"}</td>
                    <td className="py-1.5 pr-2 font-mono text-[10px]">{r.alias}</td>
                    <td className="py-1.5 pr-2 text-right font-mono text-[10px]">{fmtTokens(r.total_tokens)}</td>
                    <td className="py-1.5 pr-2 text-right text-amber-200/90">{fmtCost(r.cost_cny)}</td>
                    <td className="py-1.5 text-right">
                      <span className={r.ok ? "text-emerald-300/90" : "text-rose-300"}>{r.ok ? "OK" : "FAIL"}</span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
