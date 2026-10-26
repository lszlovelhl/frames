import { useEffect, useRef, useState } from "react";
import { api, type AnalysisJobItem } from "../api";

const POLL_MS = 3000;

const STATUS_TEXT: Record<string, string> = {
  queued: "排队中",
  running: "拆解中",
  done: "已完成",
  partial: "部分层失败",
  failed: "失败",
};

const STATUS_STYLE: Record<string, string> = {
  queued: "text-[var(--text-2)]",
  running: "text-[var(--accent-hover)]",
  done: "text-emerald-300",
  partial: "text-orange-300",
  failed: "text-rose-300",
};

/**
 * 拆解进度 - 组合方案
 *
 * 平时完全隐藏，有任务时变成右下角小图标
 * 点击展开详情面板，看完可以收起
 * 没有任务时不占任何空间
 */
export default function JobMonitor({ onOpenVideo }: { onOpenVideo: (videoId: string) => void }) {
  const [items, setItems] = useState<AnalysisJobItem[]>([]);
  const [expanded, setExpanded] = useState(false);
  const [reachable, setReachable] = useState(true);
  const timer = useRef<number | null>(null);

  useEffect(() => {
    let alive = true;
    async function tick() {
      try {
        const r = await api.activeAnalyses();
        if (!alive) return;
        setItems(r.items ?? []);
        setReachable(true);
      } catch {
        if (alive) setReachable(false);
      }
    }
    tick();
    timer.current = window.setInterval(tick, POLL_MS);
    const onVisible = () => document.visibilityState === "visible" && tick();
    document.addEventListener("visibilitychange", onVisible);
    window.addEventListener("focus", onVisible);
    return () => {
      alive = false;
      if (timer.current) window.clearInterval(timer.current);
      document.removeEventListener("visibilitychange", onVisible);
      window.removeEventListener("focus", onVisible);
    };
  }, []);

  const active = items.filter((i) => i.status === "queued" || i.status === "running");
  const recent = items.filter((i) => !(i.status === "queued" || i.status === "running")).slice(0, 5);

  const hasAnything = active.length > 0 || recent.length > 0;

  // 没有任何任务时完全隐藏
  if (!hasAnything) {
    return null;
  }

  // 折叠状态：只显示一个小图标按钮
  if (!expanded) {
    return (
      <button
        onClick={() => setExpanded(true)}
        className="fixed bottom-4 right-4 z-40 flex h-10 w-10 items-center justify-center rounded-full border border-[var(--separator-soft)] bg-[var(--bg-card)] text-[var(--text-2)] shadow-lg transition hover:border-text-[var(--accent-hover)]/40"
        title="拆解进度"
      >
        <span className={active.length ? "text-[var(--accent-hover)]" : "text-[var(--text-3)]"}>◔</span>
        {active.length > 0 && (
          <span className="absolute -top-1 -right-1 flex h-4 min-w-4 items-center justify-center rounded-full bg-text-[var(--accent)] px-1 text-[10px] font-bold text-black">
            {active.length}
          </span>
        )}
      </button>
    );
  }

  // 展开状态：完整面板
  return (
    <div className="fixed bottom-4 right-4 z-40 w-[340px] max-w-[92vw] overflow-hidden rounded-xl border border-[var(--separator-soft)] bg-[var(--bg-card)]/95 shadow-2xl backdrop-blur">
      <div className="flex items-center justify-between border-b border-[var(--separator-soft)] px-3 py-2">
        <div className="flex items-center gap-2 text-xs font-medium text-[var(--text-1)]">
          <span className={active.length ? "text-[var(--accent-hover)]" : "text-[var(--text-3)]"}>◔</span>
          拆解进度
          {active.length > 0 && (
            <span className="rounded bg-[var(--accent-hover)]/15 px-1.5 py-0.5 text-[10px] text-[var(--accent-hover)]">
              进行中 {active.length}
            </span>
          )}
        </div>
        <button onClick={() => setExpanded(false)} className="text-xs text-[var(--text-3)] hover:text-[var(--text-2)]">
          收起
        </button>
      </div>

      <div className="max-h-[46vh] overflow-y-auto px-3 py-2">
        {!reachable && <p className="text-[11px] text-[var(--text-3)]">后端未连接，稍后重试…</p>}

        {active.map((it) => (
          <div key={it.analysis_id} className="mb-2 rounded-lg bg-[var(--bg-hover)] px-2.5 py-2">
            <div className="flex items-center justify-between gap-2">
              <span className="truncate text-[12px] text-[var(--text-1)]">{it.title || "未命名视频"}</span>
              <span className={`shrink-0 text-[10px] ${STATUS_STYLE[it.status]}`}>
                {STATUS_TEXT[it.status] ?? it.status}
              </span>
            </div>
            <div className="mt-1.5 h-1 overflow-hidden rounded-full bg-[var(--bg-hover)]">
              <div
                className="h-full rounded-full bg-[var(--accent-hover)]/70 transition-all"
                style={{ width: `${Math.min(100, Math.max(3, it.progress.pct || 3))}%` }}
              />
            </div>
            <div className="mt-1 truncate text-[10px] text-[var(--text-3)]">{it.progress.message}</div>
          </div>
        ))}

        {recent.length > 0 && (
          <div className={active.length ? "mt-3" : ""}>
            <div className="mb-1 text-[10px] text-[var(--text-3)]">最近完成</div>
            {recent.map((it) => (
              <button
                key={it.analysis_id}
                onClick={() => onOpenVideo(it.video_id)}
                className="mb-1 flex w-full items-center justify-between gap-2 rounded px-2 py-1 text-left transition hover:bg-[var(--bg-hover)]"
              >
                <span className="truncate text-[11px] text-[var(--text-2)]">{it.title || "未命名视频"}</span>
                <span className={`shrink-0 text-[10px] ${STATUS_STYLE[it.status]}`}>
                  {STATUS_TEXT[it.status] ?? it.status}
                </span>
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
