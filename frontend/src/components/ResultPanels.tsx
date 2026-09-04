import type { ReactNode } from "react";
import type { AnalysisResult, ElementInfo, NoteInfo, SegmentInfo } from "../api";

const TYPE_COLORS: Record<string, string> = {
  钩子: "bg-rose-400/15 text-rose-300 border-rose-400/20",
  铺垫: "bg-zinc-400/10 text-zinc-300 border-zinc-400/20",
  冲突: "bg-orange-400/15 text-orange-300 border-orange-400/20",
  转折: "bg-violet-400/15 text-violet-300 border-violet-400/20",
  高潮: "bg-amber-400/15 text-amber-300 border-amber-400/20",
  干货: "bg-sky-400/15 text-sky-300 border-sky-400/20",
  CTA: "bg-emerald-400/15 text-emerald-300 border-emerald-400/20",
};

function segColor(type: string): string {
  return TYPE_COLORS[type] ?? "bg-zinc-400/10 text-zinc-300 border-zinc-400/20";
}

function card(label: string, children: ReactNode): ReactNode {
  return (
    <section className="rounded-xl border border-white/5 bg-[#1c1f26] p-4">
      <h3 className="mb-3 text-xs font-medium uppercase tracking-wider text-zinc-500">{label}</h3>
      {children}
    </section>
  );
}

const NOTE_TYPE_LABEL: Record<string, string> = {
  transcript: "话术",
  shot: "镜头",
  audio: "声音",
  text_overlay: "字幕花字",
  rhythm: "节奏",
  frame: "构图",
};

export function LayerBadge({ status }: { status: string }): ReactNode {
  const map: Record<string, string> = {
    done: "bg-emerald-400/15 text-emerald-300",
    partial: "bg-amber-400/15 text-amber-300",
    failed: "bg-rose-400/15 text-rose-300",
    running: "bg-sky-400/15 text-sky-300",
  };
  const label: Record<string, string> = {
    done: "已完成",
    partial: "部分完成",
    failed: "失败",
    running: "进行中",
  };
  return <span className={`rounded px-1.5 py-0.5 text-[11px] ${map[status] ?? "bg-white/10 text-zinc-300"}`}>{label[status] ?? status}</span>;
}

export function SummaryPanel({ r }: { r: AnalysisResult }): ReactNode {
  const s = (r.summary ?? {}) as Record<string, string>;
  if (!s.one_liner && !s.topic) {
    return null;
  }
  return (
    <div className="rounded-xl border border-amber-300/20 bg-gradient-to-br from-amber-300/10 to-transparent p-4">
      <div className="text-[11px] uppercase tracking-wider text-amber-200/60">一句话结论</div>
      <div className="mt-1 text-base font-medium leading-relaxed text-amber-50">{s.one_liner ?? "—"}</div>
      <div className="mt-3 grid gap-3 text-sm sm:grid-cols-3">
        <div>
          <div className="text-[11px] text-zinc-500">选题主题</div>
          <div className="text-zinc-300">{s.topic ?? "—"}</div>
        </div>
        <div>
          <div className="text-[11px] text-zinc-500">钩子假设</div>
          <div className="text-zinc-300">{s.hook_hypothesis ?? "—"}</div>
        </div>
        <div>
          <div className="text-[11px] text-zinc-500">目标人群</div>
          <div className="text-zinc-300">{s.target_audience ?? "—"}</div>
        </div>
      </div>
    </div>
  );
}

export function SegmentsPanel({ segments }: { segments: SegmentInfo[] }): ReactNode {
  if (!segments || segments.length === 0) {
    return null;
  }
  return card(`结构线 · ${segments.length} 段`, (
    <div className="flex flex-col gap-2">
      {segments.map((sg) => {
        return (
          <div key={sg.seq} className="flex gap-3 rounded-lg border border-white/5 bg-white/[0.02] p-3">
            <div className="flex w-7 shrink-0 items-center justify-center rounded bg-white/5 text-xs text-zinc-400">{sg.seq}</div>
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2">
                <span className={`rounded border px-1.5 py-0.5 text-[11px] ${segColor(sg.type)}`}>{sg.type}</span>
                {sg.title ? <span className="text-sm font-medium text-zinc-100">{sg.title}</span> : null}
                {sg.hook_point ? <span className="text-[10px] text-rose-300">HOOK</span> : null}
                {sg.payoff_point ? <span className="text-[10px] text-emerald-300">PAYOFF</span> : null}
                {typeof sg.emotion_level === "number" ? (
                  <span className="ml-auto text-[11px] text-zinc-500">情绪 {sg.emotion_level}/10</span>
                ) : null}
              </div>
              {sg.summary ? <p className="mt-1 text-xs leading-relaxed text-zinc-400">{sg.summary}</p> : null}
            </div>
          </div>
        );
      })}
    </div>
  ));
}

export function NotesPanel({ notes }: { notes: NoteInfo[] }): ReactNode {
  if (!notes || notes.length === 0) {
    return null;
  }
  return card(`精拆细节 · ${notes.length} 条`, (
    <div className="flex flex-col gap-2">
      {notes.map((n, i) => {
        return (
          <div key={i} className="rounded-lg border border-white/5 bg-white/[0.02] p-3">
            <div className="flex items-center gap-2 text-[11px]">
              <span className="rounded bg-white/5 px-1.5 py-0.5 text-zinc-300">{NOTE_TYPE_LABEL[n.note_type] ?? n.note_type}</span>
              <span className="text-zinc-500">{n.role_view}</span>
              {typeof n.confidence === "number" ? <span className="text-zinc-600">置信 {Math.round(n.confidence * 100)}%</span> : null}
            </div>
            <p className="mt-1.5 text-sm leading-relaxed text-zinc-300">{n.content}</p>
          </div>
        );
      })}
    </div>
  ));
}

export function ElementsPanel({ elements }: { elements: ElementInfo[] }): ReactNode {
  if (!elements || elements.length === 0) {
    return null;
  }
  return card(`提炼元素 · ${elements.length} 张卡片`, (
    <div className="grid gap-3 md:grid-cols-2">
      {elements.map((e) => {
        return (
          <div key={e.id} className="rounded-lg border border-white/5 bg-white/[0.02] p-3">
            <div className="flex flex-wrap items-center gap-2">
              <span className="rounded bg-sky-400/10 px-1.5 py-0.5 text-[11px] text-sky-300">{e.category}</span>
              <span className="text-sm font-medium text-zinc-100">{e.name}</span>
              {typeof e.confidence === "number" ? <span className="ml-auto text-[10px] text-zinc-600">{Math.round(e.confidence * 100)}%</span> : null}
            </div>
            {e.description ? <p className="mt-1 text-xs text-zinc-400">{e.description}</p> : null}
            {e.formula ? (
              <p className="mt-2 rounded border border-emerald-300/10 bg-emerald-300/5 p-2 font-mono text-[11px] leading-relaxed text-emerald-200/90">
                {e.formula}
              </p>
            ) : null}
            {e.evidence && e.evidence.length > 0 ? (
              <p className="mt-1.5 text-[11px] italic text-zinc-600">
                证据：{e.evidence.map((ev) => (ev.seg ? `#${ev.seg}段` : "")).join(" ")} {e.evidence[0]?.quote ?? ""}
              </p>
            ) : null}
          </div>
        );
      })}
    </div>
  ));
}

export function LayersRail({ r }: { r: AnalysisResult }): ReactNode {
  return (
    <div className="flex items-center gap-1.5 text-[11px]">
      {[1, 2, 3, 4, 5].map((n) => {
        const layer = r.layers.find((l) => l.layer === n);
        const ok = layer !== undefined && !(layer.content as Record<string, unknown>).layer_error;
        const pulsing = r.status === "running" && r.current_layer === n;
        let cls = "border-white/10 text-zinc-600";
        if (ok) {
          cls = "border-emerald-400/30 bg-emerald-400/10 text-emerald-300";
        } else if (pulsing) {
          cls = "animate-pulse border-sky-400/40 bg-sky-400/10 text-sky-300";
        }
        return (
          <span key={n} className={`flex h-6 w-6 items-center justify-center rounded-full border ${cls}`}>
            {ok ? "✓" : n}
          </span>
        );
      })}
      <span className="ml-2 flex items-center gap-2">
        <LayerBadge status={r.status} />
        {r.status === "done" ? <span className="text-zinc-500">L1-L5 完成</span> : null}
        {r.status === "partial" ? <span className="text-amber-400/80">部分层失败，可重跑</span> : null}
        {r.status === "failed" ? <span className="text-rose-400/80">执行失败</span> : null}
      </span>
    </div>
  );
}
