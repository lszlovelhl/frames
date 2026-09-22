import { useEffect, useMemo, useRef, useState } from "react";
import { BASE, api, type AnalysisResult, type ElementInfo, type FrameInfo, type SegmentInfo, type VideoItem, type VideoStatsHistoryPoint, type VideoStatsHistoryView, type VideoStatsView } from "../api";

/* ---------------- 工具 ---------------- */
function fmt(ms: number): string {
  if (!Number.isFinite(ms) || ms < 0) return "--:--";
  const s = Math.floor(ms / 1000);
  const m = Math.floor(s / 60);
  const r = Math.floor(s % 60);
  return `${m}:${String(r).padStart(2, "0")}`;
}

function fmtNum(n: number | null | undefined): string {
  if (n == null) return "--";
  const v = Number(n);
  if (Math.abs(v) >= 1e8) return `${(v / 1e8).toFixed(1)}亿`;
  if (Math.abs(v) >= 1e4) return `${(v / 1e4).toFixed(1)}万`;
  return v.toLocaleString();
}

function fmtDelta(n: number | undefined | null): string {
  if (n == null || !Number.isFinite(n)) return "—";
  if (n === 0) return "0";
  return `${n > 0 ? "+" : ""}${n.toLocaleString()}`;
}

const STAT_FIELDS: Array<[string, string]> = [
  ["view_count", "播放"],
  ["like_count", "点赞"],
  ["collect_count", "收藏"],
  ["share_count", "转发"],
  ["comment_count", "评论"],
  ["danmaku_count", "弹幕"],
];

const fmtShortTime = (iso: string | null): string =>
  iso ? new Date(iso).toLocaleString("zh-CN", { hour12: false }) : "—";

const SEG_TYPE_COLORS: Record<string, string> = {
  钩子: "bg-rose-400/15 text-rose-300 border-rose-400/20",
  铺垫: "bg-zinc-400/10 text-zinc-300 border-zinc-400/20",
  冲突: "bg-orange-400/15 text-orange-300 border-orange-400/20",
  转折: "bg-violet-400/15 text-violet-300 border-violet-400/20",
  高潮: "bg-amber-400/15 text-amber-300 border-amber-400/20",
  干货: "bg-sky-400/15 text-sky-300 border-sky-400/20",
  CTA: "bg-emerald-400/15 text-emerald-300 border-emerald-400/20",
};

const SEG_TYPE_BAR: Record<string, string> = {
  钩子: "bg-rose-400",
  铺垫: "bg-zinc-400",
  冲突: "bg-orange-400",
  转折: "bg-violet-400",
  高潮: "bg-amber-400",
  干货: "bg-sky-400",
  CTA: "bg-emerald-400",
};

const NOTE_TYPE_LABEL: Record<string, string> = {
  transcript: "话术",
  shot: "镜头",
  audio: "声音",
  text_overlay: "字幕花字",
  rhythm: "节奏",
  frame: "构图",
};

const ELEMENT_STATUS: Record<string, { label: string; cls: string; btn: string }> = {
  draft: { label: "待审", cls: "bg-white/5 text-zinc-400", btn: "text-zinc-400 hover:bg-white/5" },
  accepted: { label: "已采纳", cls: "bg-emerald-400/15 text-emerald-300", btn: "text-emerald-300 hover:bg-emerald-400/10" },
  adjusted: { label: "已纠错", cls: "bg-sky-400/15 text-sky-300", btn: "text-sky-300 hover:bg-sky-400/10" },
  rejected: { label: "已驳回", cls: "bg-rose-400/15 text-rose-300", btn: "text-rose-300 hover:bg-rose-400/10" },
};

const ELEMENT_CATEGORIES = ["选题", "钩子", "结构", "话术", "情绪", "视觉", "剪辑手法", "声音设计", "运营策略"];

function segTypeColor(type: string): string {
  return SEG_TYPE_COLORS[type] ?? "bg-zinc-400/10 text-zinc-300 border-zinc-400/20";
}
function segTypeBar(type: string): string {
  return SEG_TYPE_BAR[type] ?? "bg-zinc-500";
}

function Card({ label, extra, children }: { label: string; extra?: React.ReactNode; children: React.ReactNode }) {
  return (
    <section className="rounded-xl border border-white/5 bg-[#1c1f26] p-4">
      <div className="mb-3 flex items-center justify-between">
        <h3 className="text-xs font-medium uppercase tracking-wider text-zinc-500">{label}</h3>
        {extra}
      </div>
      {children}
    </section>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="text-[11px] text-zinc-500">{label}</div>
      <div className="mt-0.5 text-sm leading-relaxed text-zinc-300">{children}</div>
    </div>
  );
}

/* ---------------- L1 档案 ---------------- */
function L1Panel({ c }: { c: Record<string, unknown> }) {
  const oneLiner = (c.one_liner as string) || "";
  const topic = c.topic as string;
  const hook = c.hook_hypothesis as string;
  const keyPerspective = c.key_perspective as string;
  const audience = c.target_audience as string;
  const structure = Array.isArray(c.expected_structure) ? (c.expected_structure as string[]) : [];
  if (!oneLiner && !topic) return null;
  return (
    <div className="overflow-hidden rounded-2xl border border-amber-300/20 bg-gradient-to-br from-amber-300/10 via-[#1c1f26] to-transparent p-5">
      <div className="flex items-center gap-2">
        <span className="rounded bg-amber-300/10 px-1.5 py-0.5 text-[10px] font-medium text-amber-200/70">L1</span>
        <span className="text-[11px] uppercase tracking-wider text-amber-200/60">外围档案 · 一句话结论</span>
      </div>
      <div className="mt-3 text-lg font-medium leading-relaxed text-amber-50">{oneLiner || "—"}</div>
      <div className="mt-5 grid gap-x-8 gap-y-4 text-sm sm:grid-cols-2">
        <Field label="选题主题">{topic || "—"}</Field>
        <Field label="目标人群">{audience || "—"}</Field>
        <div className="sm:col-span-2">
          <Field label="钩子假设">{hook || "—"}</Field>
        </div>
        <div className="sm:col-span-2">
          <Field label="差异化视角 / 情绪切入点">{keyPerspective || "—"}</Field>
        </div>
        {structure.length > 0 && (
          <div className="sm:col-span-2">
            <div className="text-[11px] text-zinc-500">预期结构</div>
            <ol className="mt-1 space-y-1 text-sm text-zinc-300">
              {structure.map((s, i) => (
                <li key={i} className="flex gap-2">
                  <span className="text-zinc-600">{i + 1}.</span>
                  <span>{s}</span>
                </li>
              ))}
            </ol>
          </div>
        )}
      </div>
    </div>
  );
}

/* ---------------- L2 宏观 ---------------- */
function L2Panel({ c }: { c: Record<string, unknown> }) {
  if (!c.rhythm && !c.pacing_notes) return null;
  const curve = Array.isArray(c.emotion_curve) ? (c.emotion_curve as Array<{ phase?: string; level?: number }>) : [];
  return (
    <Card label="L2 宏观扫描">
      {curve.length > 0 && (
        <div className="mb-5">
          <svg viewBox="0 0 1000 200" className="w-full" style={{ height: 160 }}>
            {/* 网格线 */}
            {[0, 50, 100, 150, 200].map((y) => (
              <line key={y} x1="0" y1={y} x2="1000" y2={y} stroke="rgba(255,255,255,0.05)" strokeWidth="1" />
            ))}
            {/* 曲线 */}
            <path
              d={curve.map((pt, i) => {
                const x = (i / (curve.length - 1)) * 1000;
                const y = 200 - (Number(pt.level ?? 0) / 10) * 200;
                return i === 0 ? `M ${x} ${y}` : `L ${x} ${y}`;
              }).join(" ")}
              fill="none"
              stroke="#fbbf24"
              strokeWidth="2.5"
            />
            {/* 数据点 */}
            {curve.map((pt, i) => {
              const x = (i / (curve.length - 1)) * 1000;
              const y = 200 - (Number(pt.level ?? 0) / 10) * 200;
              return <circle key={i} cx={x} cy={y} r="3" fill="#fbbf24"><title>{`${pt.phase ?? ""} ${pt.level ?? ""}`}</title></circle>;
            })}
          </svg>
          <div className="flex gap-1.5 mt-1">
            {curve.map((pt, i) => (
              <div key={i} className="flex-1 truncate text-center text-[9px] text-zinc-600">
                {pt.phase}
              </div>
            ))}
          </div>
        </div>
      )}
      <div className="grid gap-3 text-sm sm:grid-cols-2">
        <div className="sm:col-span-2">
          <Field label="节奏总评">{String(c.rhythm ?? "—")}</Field>
        </div>
        <div className="sm:col-span-2">
          <Field label="信息密度与节奏手法">{String(c.pacing_notes ?? "—")}</Field>
        </div>
        <Field label="平台属性 / 运营痕迹">{String(c.platform_notes ?? "—")}</Field>
        <Field label="完播 / 停留设计">{String(c.retention_hypothesis ?? "—")}</Field>
      </div>
    </Card>
  );
}

/* ---------------- 时间轴 ---------------- */
function TimelineBar({
  totalMs,
  segments,
  currentMs,
  onSeek,
}: {
  totalMs: number;
  segments: SegmentInfo[];
  currentMs: number;
  onSeek: (ms: number) => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  if (!totalMs || segments.length === 0) return null;
  function handleClick(e: React.MouseEvent) {
    const rect = ref.current?.getBoundingClientRect();
    if (!rect) return;
    const ratio = Math.min(1, Math.max(0, (e.clientX - rect.left) / rect.width));
    onSeek(ratio * totalMs);
  }
  return (
    <div
      ref={ref}
      onClick={handleClick}
      className="relative h-9 w-full cursor-pointer rounded-xl bg-white/[0.03] ring-1 ring-white/5"
      title="点击跳转到对应时间点"
    >
      {segments.map((sg) => {
        const left = (sg.start_ms / totalMs) * 100;
        const width = Math.max(1, ((sg.end_ms - sg.start_ms) / totalMs) * 100);
        return (
          <div
            key={sg.seq}
            className={`absolute top-0 h-full ${segTypeBar(sg.type)} opacity-70 hover:opacity-100`}
            style={{ left: `${left}%`, width: `${width}%` }}
            title={`${sg.seq} ${sg.type} ${fmt(sg.start_ms)}-${fmt(sg.end_ms)}`}
          />
        );
      })}
      {segments.filter((sg) => sg.hook_point).map((sg) => (
        <span key={`h${sg.seq}`} className="absolute top-1 text-[10px] leading-none text-rose-300" style={{ left: `${(sg.start_ms / totalMs) * 100}%` }}>
          ⚡
        </span>
      ))}
      {segments.filter((sg) => sg.payoff_point).map((sg) => (
        <span key={`p${sg.seq}`} className="absolute top-1 text-[10px] leading-none text-emerald-300" style={{ left: `${(sg.start_ms / totalMs) * 100}%` }}>
          ★
        </span>
      ))}
      {currentMs > 0 && currentMs <= totalMs && (
        <div className="pointer-events-none absolute top-0 h-full w-0.5 bg-white shadow-[0_0_6px_rgba(255,255,255,0.9)]" style={{ left: `${(currentMs / totalMs) * 100}%` }} />
      )}
    </div>
  );
}

/* ---------------- 帧条 ---------------- */
function FrameStrip({ frames, currentMs, onSeek }: { frames: FrameInfo[]; currentMs: number; onSeek: (ms: number) => void }) {
  if (!frames || frames.length === 0) return null;
  const active = frames.findIndex((f) => currentMs >= f.start_ms && currentMs <= (f.end_ms || f.time_ms || 0));
  return (
    <div className="flex gap-1.5 overflow-x-auto pb-1">
      {frames.map((f) => (
        <button
          key={f.seq}
          onClick={() => onSeek(f.time_ms)}
          title={`${fmt(f.time_ms)} ${f.desc ?? ""}`}
          className={`relative h-14 w-20 shrink-0 overflow-hidden rounded-md ring-1 transition ${
            active === f.seq ? "ring-2 ring-amber-300" : "ring-white/10 hover:ring-white/40"
          }`}
        >
          {f.url ? <img src={`${BASE}${f.url}`} alt={`帧 ${f.seq}`} className="h-full w-full object-cover" loading="lazy" /> : <div className="h-full w-full bg-white/5" />}
          <span className="absolute bottom-0 right-0 rounded-tl bg-black/60 px-0.5 text-[8px] text-zinc-300">{fmt(f.time_ms)}</span>
        </button>
      ))}
    </div>
  );
}

/* ---------------- L3 结构线 ---------------- */
function SegmentsList({
  segments,
  currentMs,
  onSeek,
}: {
  segments: SegmentInfo[];
  currentMs: number;
  onSeek: (ms: number) => void;
}) {
  if (segments.length === 0) return null;
  const activeSeq = segments.find((s) => currentMs >= s.start_ms && currentMs < s.end_ms)?.seq;
  return (
    <Card label={`L3 结构线 · ${segments.length} 段`}>
      <div className="mb-3 flex items-center gap-2">
        <TimelineBar
          totalMs={Math.max(...segments.map((s) => s.end_ms))}
          segments={segments}
          currentMs={currentMs}
          onSeek={onSeek}
        />
        <div className="flex shrink-0 flex-col gap-0.5 text-[9px] leading-none text-zinc-600">
          <span className="text-rose-300/80">⚡ HOOK</span>
          <span className="text-emerald-300/80">★ PAYOFF</span>
        </div>
      </div>
      <div className="flex flex-col gap-2">
        {segments.map((sg) => {
          const active = activeSeq === sg.seq;
          return (
            <button
              key={sg.seq}
              onClick={() => onSeek(sg.start_ms)}
              className={`flex gap-3 rounded-lg border p-3 text-left transition ${
                active ? "border-amber-300/50 bg-amber-300/5" : "border-white/5 bg-white/[0.02] hover:border-white/15"
              }`}
            >
              <div className="flex w-7 shrink-0 items-center justify-center rounded bg-white/5 text-xs text-zinc-400">{sg.seq}</div>
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <span className={`rounded border px-1.5 py-0.5 text-[11px] ${segTypeColor(sg.type)}`}>{sg.type}</span>
                  {sg.title ? <span className="text-sm font-medium text-zinc-100">{sg.title}</span> : null}
                  <span className="ml-auto font-mono text-[10px] text-zinc-500">
                    {fmt(sg.start_ms)}–{fmt(sg.end_ms)}
                  </span>
                </div>
                {sg.summary ? <p className="mt-1 text-xs leading-relaxed text-zinc-400">{sg.summary}</p> : null}
                {(sg.hook_point || sg.payoff_point) && (
                  <div className="mt-1 flex gap-1.5 text-[10px]">
                    {sg.hook_point ? <span className="text-rose-300">⚡ 钩子点</span> : null}
                    {sg.payoff_point ? <span className="text-emerald-300">★ 兑现点</span> : null}
                  </div>
                )}
              </div>
            </button>
          );
        })}
      </div>
    </Card>
  );
}

/* ---------------- L4 细节 ---------------- */
function NotesList({
  notes,
  onSeek,
}: {
  notes: Array<{ note_type: string; start_ms: number; end_ms: number; role_view: string; content: string; confidence: number | null }>;
  onSeek: (ms: number) => void;
}) {
  if (notes.length === 0) return null;
  return (
    <Card label={`L4 精拆细节 · ${notes.length} 条`}>
      <div className="flex flex-col gap-3">
        {notes.map((n, i) => (
          <button
            key={i}
            onClick={() => (n.start_ms != null ? onSeek(n.start_ms) : undefined)}
            className="rounded-xl border border-white/5 bg-white/[0.02] p-4 text-left hover:border-white/15"
          >
            <div className="flex items-center gap-2.5 text-[11px]">
              <span className="rounded bg-white/5 px-1.5 py-0.5 text-zinc-300">{NOTE_TYPE_LABEL[n.note_type] ?? n.note_type}</span>
              <span className="text-zinc-500">{n.role_view}</span>
              {n.start_ms != null ? <span className="font-mono text-zinc-600">{fmt(n.start_ms)}</span> : null}
              {typeof n.confidence === "number" ? <span className="ml-auto text-zinc-600">置信 {Math.round(n.confidence * 100)}%</span> : null}
            </div>
            <p className="mt-2 text-sm leading-relaxed text-zinc-300">{n.content}</p>
          </button>
        ))}
      </div>
    </Card>
  );
}

/* ---------------- L5 元素（含质控） ---------------- */
function ElementCard({
  e,
  segments,
  onSeek,
  onReview,
}: {
  e: ElementInfo;
  segments: SegmentInfo[];
  onSeek: (ms: number) => void;
  onReview: (id: string, action: "accept" | "reject" | "adjust", patch?: Partial<Pick<ElementInfo, "category" | "name" | "description" | "formula">>) => Promise<void>;
}) {
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(e.name);
  const [category, setCategory] = useState(e.category);
  const [description, setDescription] = useState(e.description ?? "");
  const [formula, setFormula] = useState(e.formula ?? "");
  const [busy, setBusy] = useState(false);
  const st = ELEMENT_STATUS[e.status] ?? ELEMENT_STATUS.draft;

  async function act(action: "accept" | "reject" | "adjust") {
    setBusy(true);
    try {
      await onReview(e.id, action, action === "adjust" ? { name, category, description, formula } : undefined);
    } finally {
      setBusy(false);
      setEditing(false);
    }
  }

  return (
    <div className="flex flex-col gap-2 rounded-lg border border-white/5 bg-white/[0.02] p-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="rounded bg-sky-400/10 px-1.5 py-0.5 text-[11px] text-sky-300">{e.category}</span>
        <span className="text-sm font-medium text-zinc-100">{e.name}</span>
        {typeof e.confidence === "number" ? <span className="text-[10px] text-zinc-600">{Math.round(e.confidence * 100)}%</span> : null}
        <span className={`ml-auto rounded px-1.5 py-0.5 text-[10px] ${st.cls}`}>{st.label}</span>
      </div>
      {e.description ? <p className="text-xs text-zinc-400">{e.description}</p> : null}
      {e.formula ? (
        <p className="rounded border border-emerald-300/10 bg-emerald-300/5 p-2 font-mono text-[11px] leading-relaxed text-emerald-200/90">{e.formula}</p>
      ) : null}
      {e.evidence && e.evidence.length > 0 && (
        <div className="flex flex-col gap-1">
          {e.evidence.map((ev, i) => {
            const sg = typeof ev.seg === "number" ? segments.find((s) => s.seq === ev.seg) : undefined;
            return (
              <button
                key={i}
                onClick={() => (sg ? onSeek(sg.start_ms) : undefined)}
                title={sg ? "跳转到该证据所在段落" : undefined}
                className="rounded border border-white/5 bg-black/20 p-1.5 text-left text-[11px] italic text-zinc-500 hover:border-amber-300/30 hover:text-zinc-300"
              >
                <span className="not-italic text-zinc-600">{sg ? `#${sg.seq} ${sg.type} ${fmt(sg.start_ms)} · ` : "#? "}</span>
                {ev.quote || ""}
              </button>
            );
          })}
        </div>
      )}
      <div className="mt-0.5 flex items-center gap-1.5 border-t border-white/5 pt-2">
        <button onClick={() => void act("accept")} disabled={busy} className={`rounded px-2 py-1 text-[11px] ${st.btn}`}>
          采纳
        </button>
        <button onClick={() => setEditing(true)} disabled={busy} className="rounded px-2 py-1 text-[11px] text-zinc-400 hover:bg-white/5">
          纠错
        </button>
        <button onClick={() => void act("reject")} disabled={busy} className="rounded px-2 py-1 text-[11px] text-zinc-500 hover:bg-rose-400/10 hover:text-rose-300">
          驳回
        </button>
      </div>

      {editing && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4" onClick={() => setEditing(false)}>
          <div className="max-h-[85vh] w-full max-w-md overflow-y-auto rounded-xl border border-white/10 bg-[#17191d] p-4 shadow-2xl" onClick={(ev) => ev.stopPropagation()}>
            <div className="mb-3 text-sm font-medium text-zinc-100">纠错元素</div>
            <label className="mb-2 block">
              <span className="text-[11px] text-zinc-500">分类</span>
              <select value={category} onChange={(e) => setCategory(e.target.value)} className="mt-1 w-full rounded-lg border border-white/10 bg-[#1f2228] px-2 py-1.5 text-sm text-zinc-200 outline-none">
                {ELEMENT_CATEGORIES.map((c) => (
                  <option key={c} value={c}>{c}</option>
                ))}
              </select>
            </label>
            <label className="mb-2 block">
              <span className="text-[11px] text-zinc-500">元素名</span>
              <input value={name} onChange={(e) => setName(e.target.value)} className="mt-1 w-full rounded-lg border border-white/10 bg-[#1f2228] px-2 py-1.5 text-sm text-zinc-200 outline-none" />
            </label>
            <label className="mb-2 block">
              <span className="text-[11px] text-zinc-500">一句话说明</span>
              <textarea value={description} onChange={(e) => setDescription(e.target.value)} rows={2} className="mt-1 w-full rounded-lg border border-white/10 bg-[#1f2228] px-2 py-1.5 text-sm text-zinc-200 outline-none" />
            </label>
            <label className="mb-3 block">
              <span className="text-[11px] text-zinc-500">可复用配方</span>
              <textarea value={formula} onChange={(e) => setFormula(e.target.value)} rows={4} className="mt-1 w-full rounded-lg border border-white/10 bg-[#1f2228] px-2 py-1.5 font-mono text-xs text-emerald-200 outline-none" />
            </label>
            <div className="flex justify-end gap-2">
              <button onClick={() => setEditing(false)} className="rounded-lg px-3 py-1.5 text-xs text-zinc-400 hover:bg-white/5">
                取消
              </button>
              <button
                onClick={() => void act("adjust")}
                disabled={busy || !name.trim()}
                className="rounded-lg bg-amber-300 px-3 py-1.5 text-xs font-medium text-zinc-900 hover:bg-amber-200 disabled:opacity-40"
              >
                保存纠错
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function ElementsPanel({
  elements,
  segments,
  onSeek,
  onReview,
  onCreateWithElements,
}: {
  elements: ElementInfo[];
  segments: SegmentInfo[];
  onSeek: (ms: number) => void;
  onReview: (id: string, action: "accept" | "reject" | "adjust", patch?: Partial<Pick<ElementInfo, "category" | "name" | "description" | "formula">>) => Promise<void>;
  onCreateWithElements?: (elementIds: string[]) => void;
}) {
  if (elements.length === 0) return null;
  const counts = elements.reduce<Record<string, number>>((acc, e) => {
    acc[e.status] = (acc[e.status] ?? 0) + 1;
    return acc;
  }, {});
  const creatable = elements.filter((e) => e.status !== "rejected").map((e) => e.id);
  return (
    <Card
      label={`L5 提炼元素 · ${elements.length} 张`}
      extra={
        <div className="flex items-center gap-2 text-[10px] text-zinc-500">
          {counts.accepted ? <span className="text-emerald-300">已采纳 {counts.accepted}</span> : null}
          {counts.adjusted ? <span className="text-sky-300">已纠错 {counts.adjusted}</span> : null}
          {counts.rejected ? <span className="text-rose-300">已驳回 {counts.rejected}</span> : null}
          {counts.draft ? <span>待审 {counts.draft}</span> : null}
          {onCreateWithElements && creatable.length > 0 && (
            <button
              onClick={() => onCreateWithElements(creatable)}
              className="ml-1 rounded-md border border-emerald-300/20 bg-emerald-400/10 px-2 py-1 text-[10px] text-emerald-200 hover:bg-emerald-400/20"
              title={`带本片 ${creatable.length} 个元素去创作台，AI 基于它们生成脚本`}
            >
              用这些元素创作 ✎
            </button>
          )}
        </div>
      }
    >
      <div className="grid gap-3 md:grid-cols-2">
        {elements.map((e) => (
          <ElementCard key={e.id} e={e} segments={segments} onSeek={onSeek} onReview={onReview} />
        ))}
      </div>
    </Card>
  );
}

/* ---------------- 转写全文 ---------------- */
function TranscriptPanel({
  segments,
  text,
  onSeek,
}: {
  segments: Array<{ start: number; end: number; text: string }>;
  text: string;
  onSeek: (ms: number) => void;
}) {
  const [open, setOpen] = useState(false);
  if (segments.length === 0 && !text) return null;
  return (
    <section className="rounded-xl border border-white/5 bg-[#1c1f26]/80 p-5 shadow-[inset_0_1px_0_rgba(255,255,255,0.03)]">
      <button onClick={() => setOpen((o) => !o)} className="flex w-full items-center justify-between">
        <div className="flex items-center gap-2">
          <span className="h-3.5 w-1 rounded-full bg-amber-300/70" />
          <h3 className="text-xs font-medium uppercase tracking-wider text-zinc-500">字幕 / 转写全文 · {segments.length} 条</h3>
        </div>
        <span className="text-[10px] text-zinc-500">{open ? "收起" : "展开"}</span>
      </button>
      {open && (
        <div className="mt-4 space-y-1">
          {segments.map((s, i) => (
            <button key={i} onClick={() => onSeek(s.start * 1000)} className="flex w-full gap-3 rounded-lg px-1.5 py-1.5 text-left hover:bg-white/5">
              <span className="w-10 shrink-0 font-mono text-[10px] text-zinc-600">{fmt(s.start * 1000)}</span>
              <span className="text-xs leading-relaxed text-zinc-400">{s.text}</span>
            </button>
          ))}
        </div>
      )}
    </section>
  );
}

/* ---------------- 作者与互动数据面板 ---------------- */
const TREND_META: Record<string, { label: string; color: string }> = {
  view_count: { label: "播放", color: "#fbbf24" },
  like_count: { label: "点赞", color: "#f87171" },
  collect_count: { label: "收藏", color: "#38bdf8" },
  share_count: { label: "转发", color: "#c084fc" },
  comment_count: { label: "评论", color: "#34d399" },
  danmaku_count: { label: "弹幕", color: "#fb923c" },
  coin_count: { label: "硬币", color: "#fde047" },
};

function fmtAxisTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const hm = `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
  return `${d.getMonth() + 1}/${d.getDate()} ${hm}`;
}

/* 自绘 SVG 折线：各指标独立 min-max 归一，时间轴线性分布，端点圆点标注 */
function StatsTrendChart({ history }: { history: VideoStatsHistoryView }) {
  const W = 660;
  const H = 230;
  const pad = { top: 28, right: 16, bottom: 30, left: 16 };
  const iw = W - pad.left - pad.right;
  const ih = H - pad.top - pad.bottom;
  const pts = history.points;
  if (pts.length < 2) return null;

  const t0 = new Date(pts[0].fetched_at).getTime();
  const t1 = new Date(pts[pts.length - 1].fetched_at).getTime();
  const span = Math.max(t1 - t0, 1);
  const xAt = (i: number): number => {
    if (t1 === t0) return pad.left + (i / (pts.length - 1)) * iw;
    return pad.left + ((new Date(pts[i].fetched_at).getTime() - t0) / span) * iw;
  };

  const drawable = history.series_keys.filter((k) => {
    const key = k as keyof VideoStatsHistoryPoint;
    return pts.filter((p) => typeof p[key] === "number").length >= 2;
  });
  if (drawable.length === 0) return null;

  const series = drawable.map((k) => {
    const key = k as keyof VideoStatsHistoryPoint;
    const meta = TREND_META[k] ?? { label: k, color: "#a1a1aa" };
    const vals = pts.map((p) => p[key] as number | null);
    const nums = vals.filter((v): v is number => typeof v === "number");
    const min = Math.min(...nums);
    const max = Math.max(...nums);
    const midY = pad.top + ih / 2;
    const yAt = (v: number): number => (min === max ? midY : pad.top + (1 - (v - min) / (max - min)) * ih);
    // 中间可能存在 null 断点（如早期无硬币数据）→ 按连续段拆线
    const segs: Array<Array<{ x: number; y: number }>> = [];
    let cur: Array<{ x: number; y: number }> = [];
    pts.forEach((p, i) => {
      const v = p[key];
      if (typeof v === "number") {
        cur.push({ x: xAt(i), y: yAt(v) });
      } else if (cur.length > 0) {
        segs.push(cur);
        cur = [];
      }
    });
    if (cur.length > 0) segs.push(cur);
    return { key: k, label: meta.label, color: meta.color, last: nums[nums.length - 1], segs };
  });

  let firstRef = 0;
  for (let i = 0; i < pts.length; i++) {
    if (pts[i].phase === "baseline") {
      firstRef = i;
      break;
    }
  }
  let lastRef = pts.length - 1;
  for (let i = pts.length - 1; i >= 0; i--) {
    if (pts[i].phase === "latest") {
      lastRef = i;
      break;
    }
  }

  return (
    <div>
      <div className="mb-2 flex flex-wrap items-center gap-x-4 gap-y-1.5">
        {series.map((s) => (
          <span key={s.key} className="inline-flex items-center gap-1.5 text-[11px] text-zinc-400">
            <span className="inline-block h-0.5 w-4 rounded-full" style={{ backgroundColor: s.color }} />
            {s.label}
            <span className="font-mono text-zinc-200">{fmtNum(s.last)}</span>
          </span>
        ))}
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} className="h-auto w-full" role="img" aria-label="互动数据趋势图">
        {/* 横向网格 */}
        {[0.25, 0.5, 0.75].map((r) => (
          <line key={r} x1={pad.left} x2={W - pad.right} y1={pad.top + ih * r} y2={pad.top + ih * r} stroke="rgba(255,255,255,0.05)" strokeWidth={1} />
        ))}
        <line x1={pad.left} x2={W - pad.right} y1={pad.top + ih} y2={pad.top + ih} stroke="rgba(255,255,255,0.12)" strokeWidth={1} />
        {/* 首/末参考线 */}
        {firstRef !== lastRef && (
          <>
            <line x1={xAt(firstRef)} x2={xAt(firstRef)} y1={pad.top} y2={pad.top + ih} stroke="rgba(255,255,255,0.16)" strokeWidth={1} strokeDasharray="3 4" />
            <line x1={xAt(lastRef)} x2={xAt(lastRef)} y1={pad.top} y2={pad.top + ih} stroke="rgba(255,255,255,0.16)" strokeWidth={1} strokeDasharray="3 4" />
            <text x={xAt(firstRef)} y={pad.top - 8} fontSize={10} fill="#71717a" textAnchor={xAt(firstRef) < 60 ? "start" : "middle"}>
              初始 {fmtAxisTime(pts[firstRef].fetched_at)}
            </text>
            <text x={xAt(lastRef)} y={pad.top - 8} fontSize={10} fill="#e4e4e7" textAnchor={xAt(lastRef) > W - 90 ? "end" : "middle"}>
              当前 {fmtAxisTime(pts[lastRef].fetched_at)}
            </text>
          </>
        )}
        {/* 各序列折线 */}
        {series.map((s) =>
          s.segs.map((seg, si) => {
            const d = seg.map((p, pi) => `${pi === 0 ? "M" : "L"}${p.x.toFixed(1)} ${p.y.toFixed(1)}`).join(" ");
            return (
              <path key={`${s.key}-${si}`} d={d} fill="none" stroke={s.color} strokeWidth={1.8} strokeLinejoin="round" strokeLinecap="round" opacity={0.92} />
            );
          })
        )}
        {/* 端点圆点 */}
        {series.map((s) =>
          s.segs.map((seg, si) => {
            const dots = [seg[0]];
            const segLast = seg[seg.length - 1];
            if (segLast !== seg[0]) dots.push(segLast);
            return dots.map((p, di) => (
              <circle key={`${s.key}-${si}-${di}`} cx={p.x} cy={p.y} r={3} fill={s.color} stroke="#0c0e13" strokeWidth={1.2} />
            ));
          })
        )}
        {/* 首末时间标签 */}
        <text x={pad.left} y={H - 8} fontSize={10} fill="#71717a" textAnchor="start">
          {fmtAxisTime(pts[0].fetched_at)}
        </text>
        <text x={W - pad.right} y={H - 8} fontSize={10} fill="#71717a" textAnchor="end">
          {fmtAxisTime(pts[pts.length - 1].fetched_at)} · {pts.length} 次快照
        </text>
      </svg>
    </div>
  );
}

function StatsSection({
  videoId,
  stats,
  onRefreshed,
}: {
  videoId: string;
  stats: VideoStatsView | null;
  onRefreshed: (v: VideoStatsView) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [auto, setAuto] = useState(false);
  const [notice, setNotice] = useState("");
  const [history, setHistory] = useState<VideoStatsHistoryView | null>(null);
  const latestRef = useRef<VideoStatsView | null>(stats);
  latestRef.current = stats;
  const author = stats?.author;
  const hasView = !!stats;

  // 历史时序：详情载入或每次 stats 更新（手动/自动刷新）后重拉，保证曲线含最新快照
  useEffect(() => {
    let alive = true;
    api
      .getVideoStatsHistory(videoId)
      .then((h) => {
        if (alive) setHistory(h);
      })
      .catch(() => {
        if (alive) setHistory(null);
      });
    return () => {
      alive = false;
    };
  }, [videoId, stats?.updated_at]);

  function diffText(prev: VideoStatsView | null, next: VideoStatsView): string | null {
    const parts: string[] = [];
    for (const [k, label] of STAT_FIELDS) {
      const p = prev?.latest[k] ?? prev?.baseline[k];
      const n = next.latest[k] ?? next.baseline[k];
      if (p != null && n != null && Number(n) !== Number(p)) parts.push(`${label} ${fmtDelta(Number(n) - Number(p))}`);
    }
    if (prev?.author?.fans != null && next.author?.fans != null && next.author.fans !== prev.author.fans) {
      parts.push(`粉丝 ${fmtDelta(next.author.fans - prev.author.fans)}`);
    }
    if (prev?.author?.likes != null && next.author?.likes != null && next.author.likes !== prev.author.likes) {
      parts.push(`获赞 ${fmtDelta(next.author.likes - prev.author.likes)}`);
    }
    return parts.length > 0 ? parts.join(" · ") : null;
  }

  async function doRefresh(showBusy: boolean) {
    if (showBusy) {
      setBusy(true);
      setErr("");
    }
    try {
      const v = await api.refreshVideoStats(videoId);
      const changed = diffText(latestRef.current, v);
      latestRef.current = v;
      onRefreshed(v);
      if (changed) setNotice(`${changed}（${fmtShortTime(v.updated_at)}）`);
    } catch (e) {
      if (showBusy) setErr(e instanceof Error ? e.message : String(e));
    } finally {
      if (showBusy) setBusy(false);
    }
  }

  useEffect(() => {
    if (!auto) return;
    const t = setInterval(() => void doRefresh(false), 60_000);
    return () => clearInterval(t);
  }, [auto, videoId]);

  useEffect(() => {
    if (!notice) return;
    const t = setTimeout(() => setNotice(""), 8000);
    return () => clearTimeout(t);
  }, [notice]);

  return (
    <section className="rounded-xl border border-white/5 bg-[#1c1f26] p-5">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <span className="h-3.5 w-1 rounded-full bg-amber-300/70" />
          <span className="text-xs font-medium uppercase tracking-wider text-zinc-400">作者与互动数据</span>
        </div>
        <div className="flex items-center gap-3">
          {stats?.updated_at && <span className="text-[10px] text-zinc-600">更新于 {fmtShortTime(stats.updated_at)}</span>}
          <button
            onClick={() => setAuto((a) => !a)}
            className={`rounded-lg border px-3 py-1.5 text-xs transition ${
              auto ? "border-emerald-300/40 bg-emerald-300/10 text-emerald-200" : "border-white/10 text-zinc-400 hover:bg-white/5"
            }`}
            title="每 60 秒自动抓取，发现数据变化时高亮提醒"
          >
            {auto && <span className="mr-1 inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-emerald-300 align-middle" />}
            自动刷新
          </button>
          <button
            onClick={() => void doRefresh(true)}
            disabled={busy}
            className="rounded-lg border border-white/10 px-3 py-1.5 text-xs text-zinc-400 hover:bg-white/5 disabled:opacity-50"
          >
            {busy ? "抓取中…" : "刷新数据"}
          </button>
        </div>
      </div>

      {notice && (
        <div className="mb-3 flex items-center gap-2 rounded-lg border border-emerald-300/25 bg-emerald-300/10 px-3 py-2 text-xs text-emerald-200">
          <span className="h-1.5 w-1.5 shrink-0 animate-pulse rounded-full bg-emerald-300" />
          <span className="truncate">检测到数据变化：{notice}</span>
          <button onClick={() => setNotice("")} className="ml-auto shrink-0 text-emerald-300/50 hover:text-emerald-200">
            ✕
          </button>
        </div>
      )}

      {err && <p className="mb-3 rounded-lg border border-rose-400/20 bg-rose-400/10 px-3 py-2 text-xs text-rose-300">{err}</p>}

      {!hasView ? (
        <p className="text-sm text-zinc-500">该平台暂未接入互动数据抓取，暂无法展示。</p>
      ) : (
        <>
          {/* 作者 */}
          <div className="mb-4 flex flex-wrap items-center gap-3">
            {author?.avatar && (
              <img src={author.avatar} alt="" className="h-10 w-10 rounded-full bg-white/5 object-cover" referrerPolicy="no-referrer" />
            )}
            <div>
              <div className="text-sm font-medium text-zinc-100">{author?.name ?? "未知作者"}</div>
              <div className="mt-0.5 flex flex-wrap gap-3 text-[11px] text-zinc-500">
                <span>粉丝 {fmtNum(author?.fans)}</span>
                <span>获赞 {fmtNum(author?.likes)}</span>
                {stats.baseline_at && <span>初始于 {fmtShortTime(stats.baseline_at)}</span>}
              </div>
            </div>
          </div>

          {/* 指标对比 */}
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead>
                <tr className="border-b border-white/5 text-[10px] uppercase tracking-wider text-zinc-500">
                  <th className="py-1.5 pr-3 font-medium">指标</th>
                  <th className="py-1.5 pr-3 text-right font-medium">初始值</th>
                  <th className="py-1.5 pr-3 text-right font-medium">当前值</th>
                  <th className="py-1.5 text-right font-medium">变化</th>
                </tr>
              </thead>
              <tbody>
                {STAT_FIELDS.map(([k, label]) => {
                  const b = stats.baseline[k] ?? stats.latest[k] ?? null;
                  const c = stats.latest[k] ?? stats.baseline[k] ?? null;
                  const d = stats.diff[k] ?? (b != null && c != null ? Number(c) - Number(b) : null);
                  if (b == null && c == null) return null;
                  return (
                    <tr key={k} className="border-b border-white/[0.03] last:border-0">
                      <td className="py-2 pr-3 text-zinc-300">{label}</td>
                      <td className="py-2 pr-3 text-right text-zinc-500">{fmtNum(b)}</td>
                      <td className="py-2 pr-3 text-right text-zinc-200">{fmtNum(c)}</td>
                      <td className={`py-2 text-right font-mono ${d == null ? "text-zinc-600" : d > 0 ? "text-emerald-300" : d < 0 ? "text-rose-300" : "text-zinc-500"}`}>
                        {fmtDelta(d as number)}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          {stats.warnings?.length > 0 && (
            <p className="mt-2 text-[10px] text-amber-200/80">{stats.warnings.join("；")}</p>
          )}

          {/* 互动数据历史趋势 */}
          {history !== null && (
            <div className="mt-4 rounded-lg bg-white/[0.03] p-3">
              <div className="mb-2 text-[10px] uppercase tracking-wider text-zinc-500">互动数据趋势</div>
              {history.points.length < 2 ? (
                <p className="text-xs leading-relaxed text-zinc-500">
                  历史快照不足（当前 {history.points.length} 个时间点），至少累计 2 次刷新后才能绘制趋势曲线。
                </p>
              ) : (
                <StatsTrendChart history={history} />
              )}
            </div>
          )}

          {/* 热评 */}
          {stats.comments.length > 0 && (
            <div className="mt-4">
              <div className="mb-2 text-[10px] uppercase tracking-wider text-zinc-500">高赞热评 TOP {Math.min(stats.comments.length, 8)}</div>
              <div className="space-y-2">
                {stats.comments.slice(0, 8).map((cm) => (
                  <div key={cm.comment_id} className="rounded-lg bg-white/[0.03] p-3">
                    <div className="flex items-center gap-2 text-[11px]">
                      <span className="font-medium text-zinc-300">{cm.user_name}</span>
                      <span className="rounded bg-amber-300/10 px-1.5 py-0.5 text-[9px] text-amber-200/90">{cm.like_count.toLocaleString()} 赞</span>
                      {cm.reply_count > 0 && <span className="text-zinc-600">{cm.reply_count} 回复</span>}
                      {cm.is_top && <span className="rounded bg-white/10 px-1.5 py-0.5 text-[9px] text-zinc-300">置顶</span>}
                    </div>
                    <p className="mt-1.5 text-[13px] leading-relaxed text-zinc-400">{cm.content}</p>
                  </div>
                ))}
              </div>
            </div>
          )}
        </>
      )}
    </section>
  );
}

/* A+B 降级播放器区：无原片（auto 清理 / 未保留本地片）时展示关键帧 + 回源 */
function NoLocalMediaBox({ video, frames }: { video: VideoItem; frames: FrameInfo[] }) {
  const cleaned = !!video.media?.cleaned;
  const heroFrame = frames.find((f) => f.url);
  const hero = heroFrame?.url ?? video.cover_url;
  const src = hero ? (hero.startsWith("http") ? hero : `${BASE}${hero}`) : null;
  return (
    <div className="relative aspect-video w-full overflow-hidden bg-[#14161a]">
      {src ? (
        <img src={src} alt="关键帧预览" className="h-full w-full object-cover opacity-60" />
      ) : (
        <div className="flex h-full w-full items-center justify-center text-xs text-zinc-600">暂无本地关键帧</div>
      )}
      <div className="pointer-events-none absolute inset-x-0 bottom-0 h-24 bg-gradient-to-t from-black/80 to-transparent" />
      <div className="absolute inset-x-0 bottom-0 flex flex-wrap items-center gap-2 p-3">
        <span className="rounded bg-amber-300/90 px-2 py-0.5 text-[10px] font-semibold text-zinc-900">
          {cleaned ? "原片已清理" : "无本地原片"}
        </span>
        <span className="rounded bg-white/10 px-2 py-0.5 text-[10px] text-zinc-300">已降级为关键帧预览</span>
        {video.media?.audio_track_url ? (
          <span className="rounded bg-white/10 px-2 py-0.5 text-[10px] text-zinc-400">保留音轨 m4a</span>
        ) : null}
      </div>
      {video.url ? (
        <a
          href={video.url}
          target="_blank"
          rel="noopener noreferrer"
          className="absolute right-3 top-3 rounded-lg border border-white/15 bg-black/60 px-3 py-1.5 text-[11px] text-zinc-200 backdrop-blur transition hover:border-amber-300/40 hover:text-amber-200"
        >
          回源打开原视频 ↗
        </a>
      ) : null}
    </div>
  );
}

/* ---------------- 主组件 ---------------- */
export default function VideoBreakdown({ video, result, onCreateWithElements }: { video: VideoItem; result: AnalysisResult; onCreateWithElements?: (elementIds: string[]) => void }) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const [detail, setDetail] = useState<{
    transcript_text: string;
    transcript_segments: Array<{ start: number; end: number; text: string }>;
    frames: FrameInfo[];
  } | null>(null);
  const [elements, setElements] = useState<ElementInfo[]>([]);
  const [statsView, setStatsView] = useState<VideoStatsView | null>(null);
  const [currentMs, setCurrentMs] = useState(0);
  // 左列播放器收窄为竖条：rail=true 后鼠标移入临时展开、移出折叠；点击可固定展开
  const [rail, setRail] = useState(false);
  const [hoverOpen, setHoverOpen] = useState(false);
  // 桌面宽视口才启用双列 + 竖条；窄屏保持单列堆叠
  const [isWide, setIsWide] = useState(
    () => typeof window !== "undefined" && window.matchMedia("(min-width: 1024px)").matches,
  );
  useEffect(() => {
    const mq = window.matchMedia("(min-width: 1024px)");
    const onChange = (e: MediaQueryListEvent) => setIsWide(e.matches);
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);

  useEffect(() => {
    setDetail(null);
    setElements([]);
    setStatsView(null);
    setCurrentMs(0);
    if (video.id) {
      api
        .getVideoDetail(video.id)
        .then((d) => {
          setDetail(d.detail);
          setStatsView(d.stats ?? null);
        })
        .catch(() => setDetail(null));
    }
    setElements(result.layers.find((l) => l.layer === 5)?.elements ?? []);
  }, [video.id, result]);

  const segments = useMemo(() => result.layers.find((l) => l.layer === 3)?.segments ?? [], [result]);
  const notes = useMemo(() => result.layers.find((l) => l.layer === 4)?.notes ?? [], [result]);
  const [activeTab, setActiveTab] = useState<"script" | "curve" | "segments" | "elements" | "transcript" | "other">("script");
  const l1c = useMemo(() => (result.layers.find((l) => l.layer === 1)?.content ?? {}) as Record<string, unknown>, [result]);
  const l2c = useMemo(() => (result.layers.find((l) => l.layer === 2)?.content ?? {}) as Record<string, unknown>, [result]);
  const videoUrl = video.media?.video_url ? `${BASE}${video.media.video_url}` : null;

  function seekTo(ms: number) {
    const v = videoRef.current;
    if (!v || !Number.isFinite(ms) || ms < 0) return;
    v.currentTime = ms / 1000;
    void v.play().catch(() => undefined);
    setCurrentMs(ms);
  }

  async function reviewElement(id: string, action: "accept" | "reject" | "adjust", patch?: Partial<Pick<ElementInfo, "category" | "name" | "description" | "formula">>) {
    await api.reviewElement(id, action, patch);
    setElements((prev) => prev.map((e) => (e.id === id ? { ...e, status: action === "accept" ? "accepted" : action === "reject" ? "rejected" : "adjusted", ...(action === "adjust" && patch ? patch : {}) } : e)));
  }

  const railOpen = !rail || hoverOpen || !isWide;

  return (
    <div
      className="grid items-start gap-4"
      style={{
        gridTemplateColumns: isWide
          ? railOpen
            ? "minmax(360px, 430px) minmax(0, 1fr)"
            : "52px minmax(0, 1fr)"
          : "1fr",
        transition: "grid-template-columns 220ms ease",
      }}
    >
      {/* 左列：播放器 + 作者互动（可收窄为竖条） */}
      <div
        className="min-w-0"
        onMouseEnter={() => isWide && rail && setHoverOpen(true)}
        onMouseLeave={() => isWide && rail && setHoverOpen(false)}
      >
        {rail && !railOpen ? (
          /* 竖条态 */
          <button
            onClick={() => setRail(false)}
            title="点击固定展开 / 鼠标移入临时展开"
            className="flex min-h-[460px] w-full flex-col items-center gap-3 rounded-xl border border-white/5 bg-[#1c1f26] px-2 py-3 text-zinc-400 transition-colors hover:bg-[#232733] hover:text-zinc-200"
          >
            <span className="text-amber-300/80">▶</span>
            <span className="text-lg opacity-40">┆</span>
            {video.cover_url ? (
              <img src={video.cover_url} alt="" className="w-9 rounded-lg object-cover" referrerPolicy="no-referrer" />
            ) : (
              <span className="text-xs opacity-40">▤</span>
            )}
            <span className="whitespace-nowrap text-[10px] [writing-mode:vertical-rl] tracking-widest">{video.title || video.platform}</span>
            <span className="mt-auto whitespace-nowrap text-[9px] text-zinc-600 [writing-mode:vertical-rl]">hover 展开</span>
          </button>
        ) : (
          /* 展开态 */
          <div className="lg:sticky lg:top-4">
            <div className="overflow-hidden rounded-xl border border-white/5 bg-[#1c1f26]">
              <div className="flex items-start justify-between gap-3 px-4 pb-1 pt-4">
                <div className="min-w-0 flex-1">
                  <div className="truncate text-sm font-medium text-zinc-100">{video.title || "未命名视频"}</div>
                  <div className="mt-1 flex items-center gap-2">
                    {video.author_avatar ? (
                      <img
                        src={video.author_avatar}
                        alt=""
                        className="h-5 w-5 shrink-0 rounded-full bg-white/10 object-cover"
                        referrerPolicy="no-referrer"
                      />
                    ) : null}
                    <span className="text-[11px] text-zinc-400" title={video.author_id ?? undefined}>
                      {video.author_name ?? "未知作者"}
                    </span>
                    <span className="text-[11px] text-zinc-600">· {video.platform}</span>
                  </div>
                  <div className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[10px] text-zinc-600">
                    {video.publish_time ? <span>发布于 {fmtShortTime(video.publish_time)}</span> : null}
                    {video.subtitle_source ? (
                      <span className="rounded bg-white/5 px-1.5 py-px text-zinc-500">字幕：{video.subtitle_source}</span>
                    ) : null}
                  </div>
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  {video.url ? (
                    <a
                      href={video.url}
                      target="_blank"
                      rel="noopener noreferrer"
                      title="打开平台原视频"
                      className="rounded-lg border border-white/10 px-2 py-1 text-[10px] text-zinc-400 hover:bg-white/5 hover:text-zinc-200"
                    >
                      回源 ↗
                    </a>
                  ) : null}
                  {isWide && (
                  <button
                    onClick={() => {
                      if (rail && hoverOpen) setRail(false);
                      else setRail(true);
                    }}
                    title={rail && hoverOpen ? "固定展开" : "收窄为竖条，鼠标移入展开"}
                    className="shrink-0 rounded-lg border border-white/10 px-2 py-1 text-[10px] text-zinc-400 hover:bg-white/5 hover:text-zinc-200"
                  >
                    {rail && hoverOpen ? "固定 ◉" : "收窄 ◂"}
                  </button>
                  )}
                  </div>
              </div>

              {videoUrl ? (
                <video
                  ref={videoRef}
                  src={videoUrl}
                  controls
                  preload="metadata"
                  className="aspect-video w-full bg-black"
                  onTimeUpdate={(e) => setCurrentMs(e.currentTarget.currentTime * 1000)}
                  onSeeked={(e) => setCurrentMs(e.currentTarget.currentTime * 1000)}
                />
              ) : (
                <NoLocalMediaBox video={video} frames={detail?.frames ?? []} />
              )}

              <div className="space-y-3 p-3">
                <div className="flex flex-wrap gap-2 text-[10px] text-zinc-500">
                  {video.duration_ms != null ? <span>时长 {fmt(video.duration_ms)}</span> : null}
                  {video.media?.bpm ? <span>BPM {video.media.bpm}</span> : null}
                  {detail ? <span>{detail.frames.length} 关键帧</span> : null}
                  {video.media?.has_transcript ? <span>含转写</span> : null}
                  {video.category_guess ? <span>{video.category_guess}</span> : null}
                  {video.tags && video.tags.length > 0
                    ? video.tags.slice(0, 8).map((t) => (
                        <span key={t} className="rounded border border-amber-300/20 bg-amber-300/5 px-1.5 py-px text-[9px] text-amber-200/80">
                          #{t}
                        </span>
                      ))
                    : null}
                </div>
                {detail && detail.frames.length > 0 && (
                  <div>
                    <div className="mb-2 text-[10px] uppercase tracking-wider text-zinc-600">关键帧 · 点击跳转</div>
                    <FrameStrip frames={detail.frames} currentMs={currentMs} onSeek={seekTo} />
                  </div>
                )}
              </div>
            </div>

            {/* 作者与互动数据放在播放器下部 */}
            <div className="mt-4">
              <StatsSection videoId={video.id} stats={statsView} onRefreshed={(v) => setStatsView(v)} />
            </div>
          </div>
        )}
      </div>

      {/* 右列：五层拆解内容（编导视角 tab 切换） */}
      <div className="min-w-0 space-y-4">
        {/* tab 导航 */}
        <div className="flex gap-1 border-b border-white/10 pb-1">
          {([
            ["script", "完整脚本"],
            ["curve", "情绪曲线"],
            ["segments", "段落分析"],
            ["elements", "元素提炼"],
            ["transcript", "转写"],
            ["other", "其他"],
          ] as const).map(([key, label]) => (
            <button
              key={key}
              onClick={() => setActiveTab(key)}
              className={`rounded-t px-3 py-1.5 text-xs transition ${
                activeTab === key
                  ? "bg-amber-400/10 text-amber-300 border-b-2 border-amber-400"
                  : "text-zinc-500 hover:text-zinc-300"
              }`}
            >
              {label}
            </button>
          ))}
        </div>

        {/* tab 内容 */}
        {activeTab === "script" && (
          <FullScriptPanel
            fullScript={result.full_script}
            body={result.full_script_body}
            storyboard={result.storyboard}
          />
        )}
        {activeTab === "curve" && <L2Panel c={l2c} />}
        {activeTab === "segments" && (
          <>
            <L1Panel c={l1c} />
            <SegmentsList segments={segments} currentMs={currentMs} onSeek={seekTo} />
          </>
        )}
        {activeTab === "elements" && (
          <ElementsPanel elements={elements} segments={segments} onSeek={seekTo} onReview={reviewElement} onCreateWithElements={onCreateWithElements} />
        )}
        {activeTab === "transcript" && detail && (
          <TranscriptPanel segments={detail.transcript_segments} text={detail.transcript_text} onSeek={seekTo} />
        )}
        {activeTab === "other" && (
          <>
            <NotesList notes={notes} onSeek={seekTo} />
            <AuditCard result={result} />
          </>
        )}
      </div>
    </div>
  );
}

/** 完整脚本还原：L4.5 成文脚本 + harness 画面分镜，双栏并排 */
function FullScriptPanel({
  fullScript,
  body,
  storyboard,
}: {
  fullScript?: string;
  body?: string;
  storyboard?: Array<{
    seg: number;
    start_s: number;
    end_s: number;
    shots: string[];
    overlay: string;
    dynamics: string;
  }>;
}) {
  const showBody = body || fullScript || "";
  if (!showBody && !storyboard?.length) return null;
  return (
    <div className="rounded-xl border border-emerald-300/20 bg-gradient-to-br from-emerald-300/5 to-transparent p-4">
      <div className="flex items-center justify-between">
        <div className="text-[11px] uppercase tracking-wider text-emerald-200/70">完整脚本 · 编导还原（口播 + 画面双轨）</div>
        <span className="rounded bg-emerald-400/10 px-1.5 py-0.5 text-[10px] text-emerald-300">L4.5</span>
      </div>
      <div className="mt-2 grid gap-3 lg:grid-cols-2">
        <div className="min-w-0">
          <div className="mb-1 text-[10px] uppercase tracking-wide text-emerald-200/50">口播文案轨</div>
          <pre className="max-h-[520px] overflow-auto whitespace-pre-wrap break-words rounded-lg bg-[#14161a] p-3 font-mono text-[12px] leading-relaxed text-zinc-200">
            {showBody}
          </pre>
        </div>
        <div className="min-w-0">
          <div className="mb-1 text-[10px] uppercase tracking-wide text-emerald-200/50">画面分镜轨 · harness 帧级对齐</div>
          {storyboard?.length ? (
            <div className="max-h-[520px] space-y-2 overflow-auto pr-1">
              {storyboard.map((sb) => (
                <div key={sb.seg} className="rounded-lg border border-white/5 bg-[#14161a] p-2.5">
                  <div className="mb-1 flex items-baseline justify-between">
                    <span className="text-[11px] font-semibold text-emerald-300">
                      段{sb.seg}
                    </span>
                    <span className="text-[10px] text-zinc-500">
                      {sb.start_s}s~{sb.end_s}s
                    </span>
                  </div>
                  {sb.shots.map((s, i) => (
                    <div key={i} className="mb-1 flex gap-1.5 text-[11px] leading-relaxed text-zinc-300">
                      <span className="mt-0.5 h-1 w-1 shrink-0 rounded-full bg-emerald-400/60" />
                      <span>{s}</span>
                    </div>
                  ))}
                  {sb.overlay && (
                    <div className="mt-1 border-t border-white/5 pt-1 text-[11px] text-amber-200/80">
                      字幕：{sb.overlay}
                    </div>
                  )}
                  {sb.dynamics && (
                    <div className="mt-0.5 text-[10px] text-sky-300/70">动态：{sb.dynamics}</div>
                  )}
                </div>
              ))}
            </div>
          ) : (
            <div className="rounded-lg bg-[#14161a] p-3 text-[11px] text-zinc-500">
              画面分镜未生成（视频暂无帧级画面数据）
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function AuditField({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="min-w-0">
      <div className="mb-0.5 text-[10px] uppercase tracking-wide text-zinc-600">{label}</div>
      <div className="break-words text-xs text-zinc-300">{children}</div>
    </div>
  );
}

/** B5 审计信息：折叠区展示 analyses/analysis_layers 的 AI 生产与人工复核字段 */
function AuditCard({ result }: { result: AnalysisResult }) {
  const top: Array<[string, React.ReactNode]> = [];
  if (result.ai_confidence != null) top.push(["AI 置信度", `${Math.round(result.ai_confidence * 100)}%`]);
  if (result.reviewed_by_user != null) top.push(["人工复核", result.reviewed_by_user ? "已完成" : "未复核"]);
  const layerEntries = result.layers
    .map((l) => ({ l, has: l.model != null || l.prompt_version != null || l.raw_response != null }))
    .filter((x) => x.has);

  return (
    <details className="group rounded-xl border border-white/5 bg-[#1c1f26]">
      <summary className="cursor-pointer select-none px-4 py-3 text-xs text-zinc-400 transition-colors hover:text-zinc-200">
        <span className="mr-2 text-[10px] text-zinc-600 group-open:rotate-90 inline-block transition-transform">▶</span>
        审计信息
        <span className="ml-2 text-[10px] text-zinc-600">AI 置信度 · 人工复核 · 模型与提示词版本</span>
      </summary>
      <div className="border-t border-white/5 px-4 py-3">
        {top.length > 0 ? (
          <div className="grid grid-cols-2 gap-3 pb-3 sm:grid-cols-4">{top.map(([k, v]) => <AuditField key={k} label={k}>{v}</AuditField>)}</div>
        ) : (
          <div className="pb-3 text-xs text-zinc-500">该次分析暂未记录顶层置信度/复核信息</div>
        )}
        {layerEntries.length === 0 ? (
          <div className="text-xs text-zinc-600">各层均无 model / prompt_version / raw_response 审计字段</div>
        ) : (
          <div className="space-y-2">
            {layerEntries.map(({ l }, idx) => (
              <div key={`${l.layer}-${idx}`} className="rounded-lg bg-black/20 p-2.5">
                <div className="mb-1 text-[10px] text-zinc-500">L{l.layer} · {l.role_view}</div>
                <div className="flex flex-wrap gap-x-5 gap-y-2">
                  {l.model != null && <AuditField label="模型">{String(l.model)}</AuditField>}
                  {l.prompt_version != null && <AuditField label="提示词版本">{String(l.prompt_version)}</AuditField>}
                  {l.raw_response != null && (
                    <div className="min-w-0 flex-1">
                      <div className="mb-0.5 text-[10px] uppercase tracking-wide text-zinc-600">原始响应</div>
                      <pre className="max-h-40 overflow-auto whitespace-pre-wrap break-words rounded bg-[#14161a] px-2 py-1.5 font-mono text-[10px] leading-relaxed text-zinc-400">{String(l.raw_response)}</pre>
                    </div>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </details>
  );
}
