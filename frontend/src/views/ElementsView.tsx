import { useEffect, useState } from "react";
import {
  api,
  type ElementItem,
} from "../api";
const STATUS_META: Record<string, { label: string; cls: string; btn: string }> = {
  draft: { label: "待质控", cls: "bg-zinc-400/15 text-zinc-300", btn: "" },
  accepted: { label: "已采纳", cls: "bg-emerald-400/15 text-emerald-300", btn: "text-emerald-300 hover:bg-emerald-400/10" },
  adjusted: { label: "已纠错", cls: "bg-sky-400/15 text-sky-300", btn: "text-sky-300 hover:bg-sky-400/10" },
  rejected: { label: "已驳回", cls: "bg-rose-400/15 text-rose-300", btn: "text-rose-300 hover:bg-rose-400/10" },
};

const CATEGORIES = ["选题", "钩子", "结构", "话术", "情绪", "视觉", "剪辑手法", "声音设计", "运营策略"];
const STATUS_TABS: Array<{ key: string; label: string }> = [
  { key: "all", label: "全部" },
  { key: "draft", label: "待质控" },
  { key: "accepted", label: "已采纳" },
  { key: "adjusted", label: "已纠错" },
  { key: "rejected", label: "已驳回" },
];

interface Props {
  onOpenInLibrary?: (videoId: string) => void;
}

type SortMode = "quality" | "usage";

export default function ElementsView({ onOpenInLibrary }: Props) {
  const [status, setStatus] = useState("all");
  const [category, setCategory] = useState("all");
  const [q, setQ] = useState("");
  const [debouncedQ, setDebouncedQ] = useState("");
  const [sortBy, setSortBy] = useState<SortMode>("quality");
  const [list, setList] = useState<ElementItem[]>([]);
  const [counts, setCounts] = useState<Record<string, number>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [editing, setEditing] = useState<ElementItem | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [selectionMode, setSelectionMode] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [mixTask, setMixTask] = useState<{ mode: "mix" | "vary"; ids: string[] } | null>(null);

  useEffect(() => {
    const t = setTimeout(() => setDebouncedQ(q.trim()), 400);
    return () => clearTimeout(t);
  }, [q]);

  async function load() {
    try {
      setLoading(true);
      const data = await api.listElements({ status, category, q: debouncedQ || undefined });
      setList(data.items ?? []);
      setCounts(data.status_counts ?? {});
      setError("");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
  }, [status, category, debouncedQ]);

  async function review(id: string, action: "accept" | "reject" | "adjust", patch?: Partial<Pick<ElementItem, "category" | "name" | "description" | "formula">>) {
    setBusyId(id);
    try {
      await api.reviewElement(id, action, patch);
      setEditing(null);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusyId(null);
    }
  }

  const totalDraft = counts.draft ?? 0;
  const selectedItems = list.filter((e) => selected.has(e.id));

  function toggleSelect(id: string) {
    setSelected((prev) => {
      const s = new Set(prev);
      if (s.has(id)) s.delete(id);
      else s.add(id);
      return s;
    });
  }

  function exitSelection() {
    setSelectionMode(false);
    setSelected(new Set());
  }

  return (
    <div className="mx-auto max-w-3xl px-4 py-6 lg:px-8">
      <header className="mb-5 flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-zinc-100">元素库</h1>
          <p className="mt-1 text-sm text-zinc-500">跨片聚合提炼元素 · 质控后沉淀为创作素材</p>
        </div>
        <div className="flex items-center gap-2">
          <span className="rounded-full border border-amber-300/20 bg-amber-300/10 px-3 py-1 text-xs text-amber-200">
            {totalDraft} 待质控
          </span>
          {!selectionMode && (
            <button
              onClick={() => setSelectionMode(true)}
              className="rounded-lg border border-violet-300/20 bg-violet-400/10 px-3 py-1.5 text-xs text-violet-200 hover:bg-violet-400/20"
              title="选择 2+ 个元素做组合、选 1 个做变异，生成新元素草稿"
            >
              变异 / 组合
            </button>
          )}
          <button onClick={() => void load()} className="rounded-lg border border-white/10 px-3 py-1.5 text-xs text-zinc-400 hover:bg-white/5">
            {selectionMode ? "取消选择" : "刷新"}
          </button>
        </div>
      </header>

      {error && <p className="mb-4 rounded-lg border border-rose-400/20 bg-rose-400/10 px-3 py-2 text-sm text-rose-300">{error}</p>}

      {/* 变异/组合选择模式操作条 */}
      {selectionMode && (
        <div className="sticky top-0 z-20 -mx-1 mb-4 flex flex-wrap items-center gap-2 rounded-xl border border-violet-300/20 bg-[#191c21]/95 px-3 py-2 shadow-lg backdrop-blur">
          <span className="text-xs text-zinc-300">
            已选 <span className="font-semibold text-violet-300">{selected.size}</span> 个元素
          </span>
          {selectedItems.length > 0 && (
            <span className="max-w-[260px] truncate text-[11px] text-zinc-500">
              {selectedItems.map((e) => e.name).join("、")}
            </span>
          )}
          <div className="ml-auto flex items-center gap-2">
            <button
              disabled={selected.size !== 1}
              onClick={() => selected.size === 1 && setMixTask({ mode: "vary", ids: [...selected] })}
              className="rounded-lg border border-violet-300/20 bg-violet-400/10 px-3 py-1.5 text-xs text-violet-200 hover:bg-violet-400/20 disabled:cursor-not-allowed disabled:opacity-40"
              title="以选中的 1 个元素为基底，AI 生成同方向的变体元素"
            >
              以此变异
            </button>
            <button
              disabled={selected.size < 2}
              onClick={() => selected.size >= 2 && setMixTask({ mode: "mix", ids: [...selected] })}
              className="rounded-lg border border-amber-300/20 bg-amber-300/10 px-3 py-1.5 text-xs text-amber-200 hover:bg-amber-300/20 disabled:cursor-not-allowed disabled:opacity-40"
              title="将选中的 2+ 个元素碰撞组合，生成融合新元素"
            >
              组合生成
            </button>
          </div>
        </div>
      )}

      {/* 状态 tab */}
      <div className="mb-4 flex flex-wrap gap-1.5">
        {STATUS_TABS.map((t) => {
          const n = t.key === "all" ? list.length || 0 : counts[t.key] ?? 0;
          const active = status === t.key;
          return (
            <button
              key={t.key}
              onClick={() => setStatus(t.key)}
              className={`rounded-full px-3 py-1.5 text-xs transition ${active ? "bg-amber-300/15 text-amber-200" : "border border-white/10 text-zinc-400 hover:bg-white/5"}`}
            >
              {t.label}
              <span className={`ml-1 ${active ? "text-amber-300/70" : "text-zinc-600"}`}>{n}</span>
            </button>
          );
        })}
      </div>

      {/* 过滤行 */}
      <div className="mb-5 flex flex-wrap gap-2">
        <select
          value={category}
          onChange={(e) => setCategory(e.target.value)}
          className="rounded-lg border border-white/10 bg-[#1c1f26] px-2.5 py-1.5 text-sm text-zinc-300 outline-none"
        >
          <option value="all">全部分类</option>
          {CATEGORIES.map((c) => (
            <option key={c} value={c}>{c}</option>
          ))}
        </select>
        <select
          value={sortBy}
          onChange={(e) => setSortBy(e.target.value as SortMode)}
          title="排序方式"
          className="rounded-lg border border-white/10 bg-[#1c1f26] px-2.5 py-1.5 text-sm text-zinc-300 outline-none"
        >
          <option value="quality">质控优先</option>
          <option value="usage">热度优先</option>
        </select>
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="搜名称 / 描述 / 配方…"
          className="min-w-0 flex-1 rounded-lg border border-white/10 bg-[#1c1f26] px-3 py-1.5 text-sm text-zinc-200 placeholder-zinc-600 outline-none focus:border-amber-300/30"
        />
      </div>

      {loading && <div className="text-sm text-zinc-500">加载中…</div>}
      {!loading && list.length === 0 && (
        <div className="rounded-xl border border-dashed border-white/10 p-8 text-center text-sm text-zinc-500">
          {status === "draft" ? "没有待质控元素，全部处理完了" : "没有符合条件的元素"}
        </div>
      )}

      <div className="flex flex-col gap-3">
        {(sortBy === "usage"
          ? [...list].sort((a, b) => (b.usage_count ?? 0) - (a.usage_count ?? 0))
          : list
        ).map((e) => {
          const st = STATUS_META[e.status] ?? STATUS_META.draft;
          const busy = busyId === e.id;
          return (
            <article
              key={e.id}
              onClick={() => selectionMode && toggleSelect(e.id)}
              className={`cursor-pointer rounded-xl border bg-[#1c1f26] p-4 transition ${
                selectionMode && selected.has(e.id)
                  ? "border-violet-300/40 ring-1 ring-violet-300/30"
                  : "border-white/5 hover:border-white/10"
              }`}
            >
              <div className="flex flex-wrap items-center gap-2 text-[11px]">
                {selectionMode && (
                  <span
                    className={`flex h-4 w-4 shrink-0 items-center justify-center rounded border text-[10px] ${
                      selected.has(e.id) ? "border-violet-300 bg-violet-400/30 text-white" : "border-white/20"
                    }`}
                  >
                    {selected.has(e.id) ? "✓" : ""}
                  </span>
                )}
                <span className="rounded bg-sky-400/10 px-1.5 py-0.5 text-sky-300">{e.category}</span>
                <span className={`rounded px-1.5 py-0.5 ${st.cls}`}>{st.label}</span>
                {e.usage_count != null && e.usage_count > 0 && (
                  <span className="rounded bg-violet-400/10 px-1.5 py-0.5 text-violet-300" title="被创作项目引用的次数（版本迭代沿用不计重复）">
                    被 {e.usage_count} 个创作引用
                  </span>
                )}
                {e.video && (
                  <button
                    onClick={() => onOpenInLibrary?.(e.video!.id)}
                    className="max-w-[300px] truncate text-zinc-500 underline-offset-2 hover:text-amber-300 hover:underline"
                    title="在拆解库打开该素材"
                  >
                    {e.video.platform} · {e.video.title}
                  </button>
                )}
                <span className="ml-auto text-zinc-600">
                  {e.confidence != null ? `置信 ${Math.round(e.confidence * 100)}%` : ""}
                </span>
              </div>

              <h3 className="mt-2 text-[15px] font-semibold leading-snug text-zinc-100">{e.name}</h3>
              {e.description && <p className="mt-1.5 text-sm leading-relaxed text-zinc-400">{e.description}</p>}
              {e.formula && (
                <details className="mt-2">
                  <summary className="cursor-pointer text-xs text-zinc-500 hover:text-zinc-300">配方</summary>
                  <p className="mt-1.5 whitespace-pre-wrap rounded-lg bg-black/20 px-3 py-2 text-xs leading-relaxed text-zinc-400">{e.formula}</p>
                </details>
              )}

              {e.evidence && e.evidence.length > 0 && (
                <div className="mt-2 flex flex-col gap-1.5 border-l-2 border-amber-300/20 pl-3">
                  {e.evidence.slice(0, 2).map((ev, i) => (
                    <p key={i} className="text-xs leading-relaxed text-zinc-500">
                      {ev.quote}
                      {ev.seg != null && <span className="ml-1 text-zinc-600">〔#{ev.seg}〕</span>}
                    </p>
                  ))}
                </div>
              )}

              <div className="mt-3 flex flex-wrap gap-1.5 border-t border-white/5 pt-3">
                <button
                  disabled={busy}
                  onClick={() => void review(e.id, "accept")}
                  className={`rounded px-2.5 py-1 text-xs ${e.status === "accepted" ? "bg-emerald-400/15 text-emerald-200" : "border border-white/10 text-zinc-400 hover:border-emerald-300/30 hover:text-emerald-300"}`}
                >
                  采纳
                </button>
                <button
                  disabled={busy}
                  onClick={() => void review(e.id, "reject")}
                  className={`rounded px-2.5 py-1 text-xs ${e.status === "rejected" ? "bg-rose-400/15 text-rose-200" : "border border-white/10 text-zinc-400 hover:border-rose-300/30 hover:text-rose-300"}`}
                >
                  驳回
                </button>
                <button
                  disabled={busy}
                  onClick={() => setEditing(e)}
                  className={`rounded px-2.5 py-1 text-xs ${e.status === "adjusted" ? "bg-sky-400/15 text-sky-200" : "border border-white/10 text-zinc-400 hover:border-sky-300/30 hover:text-sky-300"}`}
                >
                  纠错
                </button>
                {e.role_view && e.role_view !== "全员" && (
                  <span className="ml-auto self-center text-[10px] text-zinc-600">角色视角：{e.role_view}</span>
                )}
              </div>
            </article>
          );
        })}
      </div>

      {editing && <AdjustModal e={editing} onClose={() => setEditing(null)} onSave={(patch) => void review(editing.id, "adjust", patch)} />}
    </div>
  );
}

function AdjustModal({ e, onClose, onSave }: { e: ElementItem; onClose: () => void; onSave: (patch: Partial<Pick<ElementItem, "category" | "name" | "description" | "formula">>) => void }) {
  const [category, setCategory] = useState(e.category);
  const [name, setName] = useState(e.name);
  const [description, setDescription] = useState(e.description ?? "");
  const [formula, setFormula] = useState(e.formula ?? "");

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4" onClick={onClose}>
      <div className="w-full max-w-lg rounded-2xl border border-white/10 bg-[#191c21] p-5" onClick={(ev) => ev.stopPropagation()}>
        <div className="mb-4 flex items-center justify-between">
          <h3 className="text-sm font-semibold text-zinc-100">纠错元素</h3>
          <button onClick={onClose} className="text-zinc-500 hover:text-zinc-300">×</button>
        </div>
        <div className="flex flex-col gap-3">
          <div className="grid grid-cols-2 gap-2">
            <label className="flex flex-col gap-1 text-xs text-zinc-500">
              分类
              <select value={category} onChange={(ev) => setCategory(ev.target.value)} className="rounded-lg border border-white/10 bg-[#1f2228] px-2 py-1.5 text-sm text-zinc-200 outline-none">
                {CATEGORIES.map((c) => (
                  <option key={c} value={c}>{c}</option>
                ))}
              </select>
            </label>
            <label className="flex flex-col gap-1 text-xs text-zinc-500">
              名称
              <input value={name} onChange={(ev) => setName(ev.target.value)} className="rounded-lg border border-white/10 bg-[#1f2228] px-2 py-1.5 text-sm text-zinc-200 outline-none" />
            </label>
          </div>
          <label className="flex flex-col gap-1 text-xs text-zinc-500">
            描述
            <textarea value={description} onChange={(ev) => setDescription(ev.target.value)} rows={2} className="rounded-lg border border-white/10 bg-[#1f2228] px-2 py-1.5 text-sm text-zinc-200 outline-none" />
          </label>
          <label className="flex flex-col gap-1 text-xs text-zinc-500">
            配方
            <textarea value={formula} onChange={(ev) => setFormula(ev.target.value)} rows={3} className="rounded-lg border border-white/10 bg-[#1f2228] px-2 py-1.5 text-sm text-zinc-200 outline-none" />
          </label>
        </div>
        <div className="mt-5 flex justify-end gap-2">
          <button onClick={onClose} className="rounded-lg px-3 py-1.5 text-sm text-zinc-400 hover:bg-white/5">取消</button>
          <button onClick={() => onSave({ category, name, description, formula })} className="rounded-lg bg-sky-400/15 px-3 py-1.5 text-sm text-sky-200 hover:bg-sky-400/25">
            保存纠错
          </button>
        </div>
      </div>
    </div>
  );
}

/* ---------------- 变异 / 组合生成 ---------------- */
function MixModal({
  mode,
  sources,
  onClose,
  onDone,
}: {
  mode: "mix" | "vary";
  sources: ElementItem[];
  onClose: () => void;
  onDone: (fresh: boolean) => void;
}) {
  const [instruction, setInstruction] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<ElementItem[] | null>(null);

  const isMix = mode === "mix";

  async function run() {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      const data = await api.mixElements(mode, sources.map((s) => s.id), instruction || undefined);
      setResult(data.items ?? []);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4" onClick={() => !busy && onClose()}>
      <div
        className="max-h-[85vh] w-full max-w-2xl overflow-y-auto rounded-2xl border border-white/10 bg-[#191c21] p-5"
        onClick={(ev) => ev.stopPropagation()}
      >
        <div className="mb-4 flex items-center justify-between">
          <h3 className="text-sm font-semibold text-zinc-100">{isMix ? "组合生成新元素" : "变异生成变体元素"}</h3>
          <button onClick={onClose} disabled={busy} className="text-zinc-500 hover:text-zinc-300 disabled:opacity-40">×</button>
        </div>

        {/* 源元素 */}
        <div className="mb-4 flex flex-wrap gap-2">
          {sources.map((s) => (
            <span key={s.id} className="inline-flex items-center gap-1.5 rounded-lg border border-white/10 bg-[#1f2228] px-2.5 py-1.5 text-xs text-zinc-300">
              <span className="rounded bg-sky-400/10 px-1 py-0.5 text-[10px] text-sky-300">{s.category}</span>
              {s.name}
            </span>
          ))}
        </div>

        {/* 指令 */}
        <label className="mb-3 block">
          <span className="text-xs text-zinc-500">
            {isMix
              ? "组合方向（可选）：希望碰撞融合出什么？例如：把“3秒破冰钩子”和“身份标签共鸣”组合成适合知识博主的开场"
              : "变异方向（可选）：想强化 / 换场景 / 换人群？例如：从口播换成图文、从短视频换到中视频"}
          </span>
          <textarea
            value={instruction}
            onChange={(ev) => setInstruction(ev.target.value)}
            rows={2}
            placeholder={isMix ? "描述你想融合出的新元素方向…" : "描述变异方向，留空则由 AI 自由发散…"}
            className="mt-1.5 w-full rounded-lg border border-white/10 bg-[#1f2228] px-3 py-2 text-sm text-zinc-200 placeholder-zinc-600 outline-none focus:border-violet-300/30"
          />
        </label>

        {error && <p className="mb-3 rounded-lg border border-rose-400/20 bg-rose-400/10 px-3 py-2 text-sm text-rose-300">{error}</p>}

        {busy && <div className="mb-3 text-sm text-zinc-400">AI 正在生成元素草稿…</div>}

        {/* 结果 */}
        {result && result.length > 0 && (
          <div className="mb-4 flex flex-col gap-2.5">
            <div className="text-xs text-zinc-500">
              已生成 {result.length} 个{isMix ? "组合" : "变体"}元素（source_type={isMix ? "combo" : "vary"}），已入库为待质控草稿：
            </div>
            {result.map((r) => (
              <div key={r.id} className="rounded-xl border border-white/10 bg-[#1f2228] p-3">
                <div className="flex flex-wrap items-center gap-2 text-[11px]">
                  <span className="rounded bg-sky-400/10 px-1.5 py-0.5 text-sky-300">{r.category}</span>
                  <span className="rounded bg-zinc-400/15 px-1.5 py-0.5 text-zinc-300">待质控</span>
                </div>
                <div className="mt-1.5 text-sm font-medium text-zinc-100">{r.name}</div>
                {r.description && <p className="mt-1 text-xs leading-relaxed text-zinc-400">{r.description}</p>}
                {r.formula && (
                  <p className="mt-1.5 whitespace-pre-wrap rounded-lg bg-black/20 px-3 py-2 font-mono text-[11px] leading-relaxed text-emerald-200/80">{r.formula}</p>
                )}
              </div>
            ))}
          </div>
        )}

        <div className="mt-2 flex justify-end gap-2">
          {result && result.length > 0 ? (
            <button
              onClick={() => onDone(true)}
              className="rounded-lg bg-amber-300 px-4 py-1.5 text-sm font-medium text-zinc-900 hover:bg-amber-200"
            >
              完成，刷新列表
            </button>
          ) : (
            <>
              <button onClick={onClose} disabled={busy} className="rounded-lg px-3 py-1.5 text-sm text-zinc-400 hover:bg-white/5 disabled:opacity-40">
                取消
              </button>
              <button
                onClick={() => void run()}
                disabled={busy}
                className="rounded-lg bg-violet-400/20 px-4 py-1.5 text-sm font-medium text-violet-200 hover:bg-violet-400/30 disabled:opacity-40"
              >
                {isMix ? `组合 ${sources.length} 个元素` : "生成变异"}
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
