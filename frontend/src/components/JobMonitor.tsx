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
  queued: "text-zinc-400",
  running: "text-amber-300",
  done: "text-emerald-300",
  partial: "text-orange-300",
  failed: "text-rose-300",
};

/**
 * 拆解进度常驻面板。
 *
 * 数据全部来自后端 /api/analyses/active（进度落库在 analyses.meta.progress），
 * 因此切换视图、切到别的应用再回来、甚至刷新浏览器，进度都不会丢。
 * 挂在 App 布局层，不随视图卸载。
 */
export default function JobMonitor({ onOpenVideo }: { onOpenVideo: (videoId: string) => void }) {
  const [items, setItems] = useState<AnalysisJobItem[]>([]);
  const [collapsed, setCollapsed] = useState(false);
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
    // 从别的应用/标签页切回来时立即刷一次，避免看到过期进度
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

  if (collapsed) {
    return (
      <button
        onClick={() => setCollapsed(false)}
        className="fixed bottom-4 right-4 z-40 flex items-center gap-2 rounded-full border border-white/10 bg-[#1c1f26] px-4 py-2 text-xs text-zinc-300 shadow-lg transition hover:border-amber-300/40"
      >
        <span className={active.length ? "text-amber-300" : "text-zinc-500"}>◔</span>
        <span>{active.length ? `拆解中 ${active.length}` : "拆解进度"}</span>
      </button>
    );
  }

  return (
    <div className="fixed bottom-4 right-4 z-40 w-[340px] max-w-[92vw] overflow-hidden rounded-xl border border-white/10 bg-[#1c1f26]/95 shadow-2xl backdrop-blur">
      <div className="flex items-center justify-between border-b border-white/5 px-3 py-2">
        <div className="flex items-center gap-2 text-xs font-medium text-zinc-200">
          <span className={active.length ? "text-amber-300" : "text-zinc-500"}>◔</span>
          拆解进度
          {active.length > 0 && (
            <span className="rounded bg-amber-300/15 px-1.5 py-0.5 text-[10px] text-amber-200">
              进行中 {active.length}
            </span>
          )}
        </div>
        <button onClick={() => setCollapsed(true)} className="text-xs text-zinc-500 hover:text-zinc-300">
          收起
        </button>
      </div>

      <div className="max-h-[46vh] overflow-y-auto px-3 py-2">
        {!reachable && <p className="text-[11px] text-zinc-500">后端未连接，稍后重试…</p>}
        {reachable && active.length === 0 && recent.length === 0 && (
          <p className="py-2 text-[11px] text-zinc-500">暂无拆解任务，粘贴链接后可批量提交</p>
        )}

        {active.map((it) => (
          <div key={it.analysis_id} className="mb-2 rounded-lg bg-white/[0.03] px-2.5 py-2">
            <div className="flex items-center justify-between gap-2">
              <span className="truncate text-[12px] text-zinc-100">{it.title || "未命名视频"}</span>
              <span className={`shrink-0 text-[10px] ${STATUS_STYLE[it.status]}`}>
                {STATUS_TEXT[it.status] ?? it.status}
              </span>
            </div>
            <div className="mt-1.5 h-1 overflow-hidden rounded-full bg-white/10">
              <div
                className="h-full rounded-full bg-amber-300/70 transition-all"
                style={{ width: `${Math.min(100, Math.max(3, it.progress.pct || 3))}%` }}
              />
            </div>
            <div className="mt-1 truncate text-[10px] text-zinc-500">{it.progress.message}</div>
          </div>
        ))}

        {recent.length > 0 && (
          <div className={active.length ? "mt-3" : ""}>
            <div className="mb-1 text-[10px] text-zinc-600">最近完成</div>
            {recent.map((it) => (
              <button
                key={it.analysis_id}
                onClick={() => onOpenVideo(it.video_id)}
                className="mb-1 flex w-full items-center justify-between gap-2 rounded px-2 py-1 text-left transition hover:bg-white/5"
              >
                <span className="truncate text-[11px] text-zinc-400">{it.title || "未命名视频"}</span>
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
