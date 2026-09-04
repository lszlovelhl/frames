import { useState } from "react";
import { api, type AnalysisResult, type VideoItem } from "../api";
import { ElementsPanel, LayersRail, NotesPanel, SegmentsPanel, SummaryPanel } from "../components/ResultPanels";

const field =
  "w-full rounded-lg border border-white/10 bg-white/[0.03] px-3 py-2 text-sm text-zinc-100 placeholder-zinc-600 outline-none transition focus:border-amber-300/50";

const AUTO_STEPS = [
  "自动识别平台（B站 / 抖音 / YouTube …）",
  "下载视频并读取元数据",
  "语音转文字：全量 ASR 获取文案",
  "按内容分段抽帧 + 视觉模型逐段画面理解",
  "声学分析：BGM 分离、响度 / 情绪曲线",
  "多模态融合 → 五层专业拆解（L1 建档 → L5 元素）",
];

export default function BreakdownView() {
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [stage, setStage] = useState("");
  const [error, setError] = useState("");
  const [video, setVideo] = useState<VideoItem | null>(null);
  const [result, setResult] = useState<AnalysisResult | null>(null);

  async function run() {
    const link = url.trim();
    if (!link) {
      setError("请先粘贴视频链接");
      return;
    }
    setBusy(true);
    setError("");
    setResult(null);
    try {
      setStage("识别平台并建档…");
      const v = await api.createVideo({ url: link });
      setVideo(v);
      setStage("自动采集多模态素材（下载 / 转写 / 抽帧 / 视觉 / BGM）…首次可能较久");
      const r = await api.runAnalysis(v.id, "pro", 5);
      setResult(r);
      setStage("");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  const m = video?.media;

  return (
    <div className="mx-auto max-w-6xl px-8 py-8">
      <header className="mb-6">
        <h1 className="text-xl font-semibold text-zinc-100">拆解工作台</h1>
        <p className="mt-1 text-sm text-zinc-500">只填视频链接，系统自动抓取并多模态拆解（画面 + 语音 + 文案 + 音乐），无需手动粘贴任何素材</p>
      </header>

      <div className="rounded-xl border border-white/5 bg-[#1c1f26] p-6">
        <div className={field + " flex items-center gap-2 !border-white/15 !bg-white/[0.05] !px-4 !py-3.5"}>
          <span className="text-amber-300/80">🔗</span>
          <input
            className="w-full bg-transparent text-[15px] text-zinc-50 placeholder-zinc-600 outline-none"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && !busy && run()}
            placeholder="粘贴 B站 / 抖音 / YouTube 视频链接，回车即可拆解"
            disabled={busy}
          />
        </div>
        <div className="mt-3 flex flex-wrap items-center justify-between gap-3">
          <ul className="flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-zinc-500">
            {AUTO_STEPS.map((s, i) => (
              <li key={i} className="flex items-center gap-1">
                <span className="text-amber-300/60">{i + 1}</span>
                <span>{s}</span>
              </li>
            ))}
          </ul>
          <button
            onClick={run}
            disabled={busy}
            className="shrink-0 rounded-lg bg-amber-300 px-6 py-2.5 text-sm font-semibold text-zinc-900 transition hover:bg-amber-200 disabled:cursor-not-allowed disabled:opacity-40"
          >
            {busy ? (stage || "处理中…") : "一键多模态拆解"}
          </button>
        </div>
        {error && <p className="mt-4 rounded-lg border border-rose-400/20 bg-rose-400/10 px-3 py-2 text-sm text-rose-300">{error}</p>}
      </div>

      {video && !result && (
        <div className="mt-6 rounded-xl border border-white/5 bg-[#1c1f26] p-5">
          <div className="flex flex-wrap items-center gap-2 text-sm">
            <span className="max-w-[60%] truncate font-medium text-zinc-100">{video.title || "未命名视频"}</span>
            <span className="rounded bg-white/5 px-2 py-0.5 text-[11px] text-zinc-400">{video.platform}</span>
            {video.author_name && <span className="text-xs text-zinc-500">{video.author_name}</span>}
          </div>
          {m && (
            <div className="mt-3 flex flex-wrap gap-2 text-[11px]">
              <Chip on={!!m.video_path} label="视频已下载" />
              <Chip on={m.has_transcript} label={`语音文案 ${m.transcript_segments} 段`} />
              <Chip on={(m.frames ?? 0) > 0} label={`画面理解 ${m.frames} 帧`} />
              <Chip on={m.bpm != null} label={m.bpm != null ? `BPM ${m.bpm}` : "BGM/声学分析中"} />
              <Chip on={m.bgm_ok} label="BGM 已分离" />
              <span className="text-zinc-600">{busy ? stage : "等待拆解…"}</span>
            </div>
          )}
          <div className="mt-3">
            <LayersRail r={{ status: "running", current_layer: 1, layers: [], id: video.id, video_id: video.id, summary: {}, meta: {} }} />
          </div>
        </div>
      )}

      {result && (
        <div className="mt-6 space-y-4">
          <div className="flex items-center justify-between rounded-xl border border-white/5 bg-[#1c1f26] px-4 py-3">
            <div className="min-w-0">
              <div className="truncate text-sm font-medium text-zinc-100">{video?.title ?? result.video?.title}</div>
              <div className="text-[11px] text-zinc-500">
                {video?.platform} · {video?.author_name ?? "未知作者"} ·{" "}
                {m ? `文案 ${m.transcript_segments} 段 / 画面 ${m.frames} 帧 / ${m.bpm != null ? "BPM " + m.bpm + " / " : ""}${m.bgm_ok ? "BGM 已分离" : "BGM 未启用"}` : video?.created_at?.slice(0, 10)}
              </div>
            </div>
            <LayersRail r={result} />
          </div>
          <SummaryPanel r={result} />
          <SegmentsPanel segments={result.layers.find((l) => l.layer === 3)?.segments ?? []} />
          <NotesPanel notes={result.layers.find((l) => l.layer === 4)?.notes ?? []} />
          <ElementsPanel elements={result.layers.find((l) => l.layer === 5)?.elements ?? []} />
        </div>
      )}
    </div>
  );
}

function Chip({ on, label }: { on: boolean; label: string }) {
  return (
    <span
      className={
        "rounded px-2 py-0.5 " +
        (on ? "border border-emerald-400/20 bg-emerald-400/10 text-emerald-300" : "border border-white/10 bg-white/5 text-zinc-500")
      }
    >
      {on ? "✓ " : ""}
      {label}
    </span>
  );
}
