import { useCallback, useEffect, useState } from "react";
import { api, type BillingAccountResp } from "../api";

const ACTION_LABEL: Record<string, string> = {
  breakdown_short: "短视频拆解",
  breakdown_long: "长视频拆解",
  guide_generate: "创作指南",
  chat: "AI 对话",
};

const TX_TYPE_LABEL: Record<string, string> = {
  recharge: "充值",
  consume: "扣点",
  free_grant: "赠送",
  refund: "退款",
  free_usage: "免计费",
};

function fmtTime(iso: string | null): string {
  if (!iso) return "--";
  const d = new Date(iso);
  const p = (n: number) => String(n).padStart(2, "0");
  return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

function fmtPoints(n: number): string {
  return Number(n).toLocaleString();
}

export default function BillingPanel() {
  const [data, setData] = useState<BillingAccountResp | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const d = await api.billingAccount();
      setData(d);
      setError("");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const doRecharge = async (key: string, name: string) => {
    setBusy(true);
    setError("");
    try {
      await api.billingRecharge(key);
      await load();
      const pkg = data?.packages.find((p) => p.key === key);
      alert(`已模拟充值成功：${name}套餐 ${pkg ? `${pkg.amount_cny} 元 / ${pkg.points} 点` : ""}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const doFreeClaim = async () => {
    setBusy(true);
    setError("");
    try {
      const before = data?.account.balance_points ?? 0;
      await api.billingFreeClaim();
      await load();
      const gained = Math.max(0, (data?.account.balance_points ?? 0) - before);
      alert(`免费体验点数已到账：+${gained} 点`);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  if (!data) {
    return (
      <div className="mb-4 rounded-xl border border-white/5 bg-[#1c1f26] p-4 text-sm text-zinc-500">
        {error ? <span className="text-rose-300">{error}</span> : "点数账户加载中…"}
      </div>
    );
  }

  const { account, packages, action_points, recent_transactions } = data;
  const freeMode = data.billing?.mode === "free";
  const dailyPct = Math.min(100, Math.round((account.today_consumed / account.daily_limit) * 100));

  return (
    <div className="mb-5 rounded-2xl border border-amber-200/15 bg-gradient-to-br from-[#23262f] to-[#1a1d24] p-4 lg:p-5">
      {error && <p className="mb-3 rounded-lg border border-rose-400/20 bg-rose-400/10 px-3 py-2 text-sm text-rose-300">{error}</p>}

      <div className="flex flex-wrap items-start justify-between gap-4">
        {/* 左：点数余额 */}
        <div className="min-w-[220px]">
          <div className="flex items-center gap-2 text-[11px] uppercase tracking-wider text-zinc-500">
            点数账户
            {freeMode ? (
              <span className="rounded-full bg-emerald-400/15 px-2 py-0.5 text-[10px] font-medium text-emerald-300">免计费模式</span>
            ) : (
              <span className="rounded-full bg-amber-300/15 px-2 py-0.5 text-[10px] font-medium text-amber-200">模拟计费</span>
            )}
          </div>
          <div className="mt-1 flex items-baseline gap-2">
            <span className="text-3xl font-bold text-amber-200">{fmtPoints(account.balance_points)}</span>
            <span className="text-sm text-zinc-500">点</span>
          </div>
          <div className="mt-1 flex flex-wrap gap-x-4 gap-y-0.5 text-[11px] text-zinc-500">
            <span>今日已用 {fmtPoints(account.today_consumed)} / {fmtPoints(account.daily_limit)}</span>
            <span>累计充值 {fmtPoints(account.total_recharged_points)}</span>
            <span>累计消耗 {fmtPoints(account.total_consumed_points)}</span>
            {freeMode && (
              <span className="text-emerald-300/80">
                免计费期间已用 {fmtPoints(data.billing?.free_used_points ?? 0)} 点（未扣余额）
              </span>
            )}
          </div>
          <div className="mt-2 h-1 w-full max-w-[240px] overflow-hidden rounded-full bg-white/5">
            <div className="h-full rounded-full bg-amber-300/70" style={{ width: `${dailyPct}%` }} />
          </div>
          {!account.free_claimed && (
            <button
              onClick={() => void doFreeClaim()}
              disabled={busy}
              className="mt-3 rounded-lg bg-emerald-400 px-3 py-1.5 text-xs font-medium text-black hover:bg-emerald-300 disabled:opacity-50"
            >
              领取免费体验 100 点
            </button>
          )}
        </div>

        {/* 中：动作价格表 */}
        <div className="min-w-[180px]">
          <div className="text-[11px] uppercase tracking-wider text-zinc-500">动作 · 点数</div>
          <ul className="mt-2 space-y-1 text-xs">
            {action_points.map((a) => (
              <li key={a.action} className="flex items-center justify-between gap-3">
                <span className="text-zinc-400">{ACTION_LABEL[a.action] ?? a.action}</span>
                <span className="font-medium text-zinc-200">{fmtPoints(a.points)}</span>
              </li>
            ))}
          </ul>
        </div>

        {/* 右：套餐 */}
        <div className="min-w-[220px] flex-1">
          <div className="text-[11px] uppercase tracking-wider text-zinc-500">套餐充值（模拟到账）</div>
          <div className="mt-2 grid gap-2 sm:grid-cols-3">
            {packages.map((p) => (
              <button
                key={p.key}
                onClick={() => void doRecharge(p.key, p.name)}
                disabled={busy}
                className="group rounded-xl border border-white/10 bg-white/[0.03] p-3 text-left transition hover:border-amber-300/40 hover:bg-amber-300/5 disabled:opacity-50"
              >
                <div className="text-xs font-medium text-zinc-200 group-hover:text-amber-200">{p.name}</div>
                <div className="mt-1 text-lg font-semibold text-amber-200">¥{p.amount_cny}</div>
                <div className="text-[10px] text-zinc-500">{fmtPoints(p.points)} 点</div>
              </button>
            ))}
          </div>
          <p className="mt-2 text-[10px] text-zinc-600">演示环境为模拟充值，接入真实支付后自动切换为在线支付。</p>
        </div>
      </div>

      {/* 最近流水 */}
      {recent_transactions.length > 0 && (
        <div className="mt-4 border-t border-white/5 pt-3">
          <div className="mb-2 text-[11px] uppercase tracking-wider text-zinc-500">最近流水</div>
          <div className="grid gap-1.5 sm:grid-cols-2">
            {recent_transactions.slice(0, 6).map((t) => (
              <div key={t.id} className="flex items-center justify-between gap-3 rounded-lg bg-white/[0.03] px-3 py-1.5 text-xs">
                <div className="min-w-0">
                  <span className={`mr-2 rounded px-1.5 py-0.5 text-[10px] ${t.type === "consume" ? "bg-rose-400/10 text-rose-300" : t.type === "recharge" ? "bg-emerald-400/10 text-emerald-300" : "bg-sky-400/10 text-sky-300"}`}>
                    {TX_TYPE_LABEL[t.type] ?? t.type}
                  </span>
                  <span className="text-zinc-300">{t.note || t.action}</span>
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  <span className={`font-mono font-medium ${t.points >= 0 ? "text-emerald-300" : "text-rose-300"}`}>
                    {t.points >= 0 ? "+" : ""}{fmtPoints(t.points)}
                  </span>
                  <span className="text-[10px] text-zinc-600">{fmtTime(t.created_at)}</span>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
