import { useEffect, useMemo, useRef, useState } from "react";
import { BASE, api, type AnalysisResult, type ElementInfo, type FrameInfo, type SegmentInfo, type VideoItem } from "../api";

/* ---------------- 工具 ---------------- */
function fmt(ms: number): string {
  if (!Number.isFinite(ms) || ms < 0) return "--:--";
  const s = Math.floor(ms / 1000);
  const m = Math.floor(s / 60);
  const r = Math.floor(s % 60);
  return `${m}:${String(r).padStart(2, "0")}`;
}

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
          <div className="mb-2 flex items-end gap-1.5" style={{ height: 80 }}>
            {curve.map((pt, i) => {
              const h = Math.max(8, Math.min(72, Number(pt.level ?? 0) * 8));
              return (
                <div key={i} className="flex flex-1 flex-col justify-end">
                  <div className="w-full rounded-t bg-gradient-to-t from-amber-400/40 to-amber-300/70" style={{ height: h }} title={`${pt.phase ?? ""} ${pt.level ?? ""}`} />
                </div>
              );
            })}
          </div>
          <div className="flex gap-1.5">
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
}: {
  elements: ElementInfo[];
  segments: SegmentInfo[];
  onSeek: (ms: number) => void;
  onReview: (id: string, action: "accept" | "reject" | "adjust", patch?: Partial<Pick<ElementInfo, "category" | "name" | "description" | "formula">>) => Promise<void>;
}) {
  if (elements.length === 0) return null;
  const counts = elements.reduce<Record<string, number>>((acc, e) => {
    acc[e.status] = (acc[e.status] ?? 0) + 1;
    return acc;
  }, {});
  return (
    <Card
      label={`L5 提炼元素 · ${elements.length} 张`}
      extra={
        <div className="flex gap-2 text-[10px] text-zinc-500">
          {counts.accepted ? <span className="text-emerald-300">已采纳 {counts.accepted}</span> : null}
          {counts.adjusted ? <span className="text-sky-300">已纠错 {counts.adjusted}</span> : null}
          {counts.rejected ? <span className="text-rose-300">已驳回 {counts.rejected}</span> : null}
          {counts.draft ? <span>待审 {counts.draft}</span> : null}
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

/* ---------------- 主组件 ---------------- */
export default function VideoBreakdown({ video, result }: { video: VideoItem; result: AnalysisResult }) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const [detail, setDetail] = useState<{
    transcript_text: string;
    transcript_segments: Array<{ start: number; end: number; text: string }>;
    frames: FrameInfo[];
  } | null>(null);
  const [elements, setElements] = useState<ElementInfo[]>([]);
  const [currentMs, setCurrentMs] = useState(0);

  useEffect(() => {
    setDetail(null);
    setElements([]);
    setCurrentMs(0);
    if (video.id) {
      api.getVideoDetail(video.id).then((d) => setDetail(d.detail)).catch(() => setDetail(null));
    }
    setElements(result.layers.find((l) => l.layer === 5)?.elements ?? []);
  }, [video.id, result]);

  const segments = useMemo(() => result.layers.find((l) => l.layer === 3)?.segments ?? [], [result]);
  const notes = useMemo(() => result.layers.find((l) => l.layer === 4)?.notes ?? [], [result]);
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

  const stats = (video.stats_snapshot ?? {}) as Record<string, number>;
  const statsLabel: Array<[string, string]> = [
    ["play", "播放"],
    ["like", "点赞"],
    ["collect", "收藏"],
    ["share", "转发"],
    ["comment", "评论"],
    ["danmaku", "弹幕"],
  ];

  return (
    <div className="grid gap-4 lg:grid-cols-[minmax(0,420px)_1fr]">
      {/* 左列：播放器 + 帧证据 + 素材数据 */}
      <div className="lg:sticky lg:top-4 lg:self-start">
        <div className="overflow-hidden rounded-xl border border-white/5 bg-[#1c1f26]">
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
            <div className="flex aspect-video w-full items-center justify-center bg-black text-xs text-zinc-600">无本地媒体文件</div>
          )}
          <div className="space-y-3 p-3">
            <div>
              <div className="text-sm font-medium leading-snug text-zinc-100">{video.title}</div>
              <div className="mt-0.5 text-[11px] text-zinc-500">
                {video.author_name ?? "未知作者"} · {video.platform}
              </div>
            </div>
            <div className="flex flex-wrap gap-2 text-[10px] text-zinc-500">
              {video.duration_ms != null ? <span>时长 {fmt(video.duration_ms)}</span> : null}
              {video.media?.bpm ? <span>BPM {video.media.bpm}</span> : null}
              {detail ? <span>{detail.frames.length} 关键帧</span> : null}
              {video.media?.has_transcript ? <span>含转写</span> : null}
              {video.category_guess ? <span>{video.category_guess}</span> : null}
            </div>
            <div className="flex flex-wrap gap-2">
              {statsLabel.map(([k, label]) =>
                stats[k] ? (
                  <span key={k} className="rounded bg-white/5 px-2 py-0.5 text-[10px] text-zinc-400">
                    {label} {stats[k].toLocaleString()}
                  </span>
                ) : null,
              )}
            </div>
            {detail && detail.frames.length > 0 && (
              <div>
                <div className="mb-2 text-[10px] uppercase tracking-wider text-zinc-600">关键帧 · 点击跳转</div>
                <FrameStrip frames={detail.frames} currentMs={currentMs} onSeek={seekTo} />
              </div>
            )}
          </div>
        </div>
      </div>

      {/* 右列：五层详情 */}
      <div className="min-w-0 space-y-6">
        <L1Panel c={l1c} />
        <L2Panel c={l2c} />
        <SegmentsList segments={segments} currentMs={currentMs} onSeek={seekTo} />
        <NotesList notes={notes} onSeek={seekTo} />
        <ElementsPanel elements={elements} segments={segments} onSeek={seekTo} onReview={reviewElement} />
        {detail && <TranscriptPanel segments={detail.transcript_segments} text={detail.transcript_text} onSeek={seekTo} />}
      </div>
    </div>
  );
}
