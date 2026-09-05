import { useEffect, useState } from "react";
import { api, type AnalysisResult, type VideoItem } from "../api";
import VideoBreakdown from "../components/VideoBreakdown";
import { LayerBadge, LayersRail } from "../components/ResultPanels";

interface Props {
  focusVideoId?: string | null;
  onFocusConsumed?: () => void;
}

export default function LibraryView({ focusVideoId, onFocusConsumed }: Props) {
  const [videos, setVideos] = useState<VideoItem[]>([]);
  const [selected, setSelected] = useState<VideoItem | null>(null);
  const [result, setResult] = useState<AnalysisResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  async function load() {
    try {
      setLoading(true);
      const data = await api.listVideos();
      setVideos(data.videos ?? []);
      setError("");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
  }, []);

  useEffect(() => {
    if (!focusVideoId || videos.length === 0) return;
    const v = videos.find((x) => x.id === focusVideoId);
    if (!v) return;
    setSelected(v);
    setResult(null);
    onFocusConsumed?.();
    if (v.latest_analysis) {
      api
        .getAnalysis(v.latest_analysis.id)
        .then((r) => setResult(r))
        .catch((e) => setError(e instanceof Error ? e.message : String(e)));
    }
  }, [focusVideoId, videos, onFocusConsumed]);

  async function openAnalysis(v: VideoItem) {
    setSelected(v);
    setResult(null);
    if (v.latest_analysis) {
      try {
        const r = await api.getAnalysis(v.latest_analysis.id);
        setResult(r);
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      }
    }
  }

  return (
    <div className="mx-auto max-w-6xl px-8 py-8">
      <header className="mb-6 flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-zinc-100">拆解库</h1>
          <p className="mt-1 text-sm text-zinc-500">已建档素材与最近一次拆解状态</p>
        </div>
        <button onClick={() => void load()} className="rounded-lg border border-white/10 px-3 py-1.5 text-xs text-zinc-400 hover:bg-white/5">
          刷新
        </button>
      </header>

      {error && <p className="mb-4 rounded-lg border border-rose-400/20 bg-rose-400/10 px-3 py-2 text-sm text-rose-300">{error}</p>}

      <div className="grid gap-6 lg:grid-cols-[360px_1fr]">
        <div className="flex flex-col gap-2">
          {loading && <div className="text-sm text-zinc-500">加载中…</div>}
          {!loading && videos.length === 0 && (
            <div className="rounded-xl border border-dashed border-white/10 p-6 text-center text-sm text-zinc-500">还没有拆解记录，去工作台建一条吧</div>
          )}
          {videos.map((v) => (
            <button
              key={v.id}
              onClick={() => void openAnalysis(v)}
              className={`rounded-xl border p-3 text-left transition ${
                selected?.id === v.id ? "border-amber-300/40 bg-amber-300/5" : "border-white/5 bg-[#1c1f26] hover:bg-white/[0.04]"
              }`}
            >
              <div className="flex items-center gap-2">
                <span className="rounded bg-white/5 px-1.5 py-0.5 text-[10px] uppercase text-zinc-400">{v.platform}</span>
                {v.latest_analysis && <LayerBadge status={v.latest_analysis.status} />}
              </div>
              <div className="mt-1.5 line-clamp-2 text-sm font-medium text-zinc-200">{v.title}</div>
              <div className="mt-1 text-[11px] text-zinc-500">{v.author_name ?? "未知作者"} · {v.created_at.slice(0, 10)}</div>
            </button>
          ))}
        </div>

        <div>
          {!selected && <div className="rounded-xl border border-dashed border-white/10 p-10 text-center text-sm text-zinc-600">选择左侧素材查看拆解详情</div>}
          {selected && !result && <div className="text-sm text-zinc-500">{selected.latest_analysis ? "读取拆解结果…" : "该素材尚未拆解，去工作台发起。"}</div>}
          {result && (
            <div className="space-y-4">
              <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-white/5 bg-[#1c1f26] px-4 py-3">
                <div className="min-w-0">
                  <div className="truncate text-sm font-medium text-zinc-100">{selected?.title}</div>
                  <div className="text-[11px] text-zinc-500">拆解 #{result.id.slice(0, 8)}</div>
                </div>
                <LayersRail r={result} />
              </div>
              <VideoBreakdown video={selected!} result={result} />
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
