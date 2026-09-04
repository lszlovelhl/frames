import { useState } from "react";
import { api, type AnalysisResult, type VideoItem } from "../api";
import { ElementsPanel, LayersRail, NotesPanel, SegmentsPanel, SummaryPanel } from "../components/ResultPanels";

const PLATFORMS = ["bilibili", "douyin", "xhs", "youtube", "weixin", "kuaishou"];
const CATEGORIES = ["知识口播", "剧情短剧", "美妆测评", "生活Vlog", "游戏解说", "带货种草", ""];

const field =
  "w-full rounded-lg border border-white/10 bg-white/[0.03] px-3 py-2 text-sm text-zinc-100 placeholder-zinc-600 outline-none transition focus:border-amber-300/50";
const label = "mb-1 block text-[11px] text-zinc-500";

export default function BreakdownView() {
  const [form, setForm] = useState({
    platform: "bilibili",
    title: "",
    url: "",
    author_name: "",
    tags: "",
    category_guess: "知识口播",
    subtitle_text: "",
    model: "flash",
  });
  const [busy, setBusy] = useState(false);
  const [stage, setStage] = useState("");
  const [error, setError] = useState("");
  const [video, setVideo] = useState<VideoItem | null>(null);
  const [result, setResult] = useState<AnalysisResult | null>(null);

  const set = (k: keyof typeof form) => (v: string) => setForm((f) => ({ ...f, [k]: v }));

  async function run() {
    if (!form.title.trim() && !form.url.trim()) {
      setError("请至少填写标题或链接");
      return;
    }
    if (!form.subtitle_text.trim()) {
      setError("本版拆解需要字幕/旁白全文作为素材（自动抓取将在后续版本接入）");
      return;
    }
    setBusy(true);
    setError("");
    setResult(null);
    try {
      setStage("建档…");
      const v = await api.createVideo({
        platform: form.platform,
        url: form.url || `https://example.com/manual-${Date.now()}`,
        title: form.title || "未命名视频",
        author_name: form.author_name || undefined,
        tags: form.tags ? form.tags.split(/[,，]/).map((t) => t.trim()).filter(Boolean) : [],
        category_guess: form.category_guess || undefined,
        subtitle_text: form.subtitle_text,
      });
      setVideo(v);
      setStage("AI 五层拆解中（约 1-3 分钟，逐层调用模型）…");
      const r = await api.runAnalysis(v.id, form.model, 5);
      setResult(r);
      setStage("");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mx-auto max-w-6xl px-8 py-8">
      <header className="mb-6">
        <h1 className="text-xl font-semibold text-zinc-100">拆解工作台</h1>
        <p className="mt-1 text-sm text-zinc-500">粘贴视频信息与字幕全文 → 一键产出五层拆解（顶层预判 → 宏观快扫 → 结构线 → 精拆细节 → 元素提炼）</p>
      </header>

      <div className="rounded-xl border border-white/5 bg-[#1c1f26] p-5">
        <div className="grid gap-4 md:grid-cols-2">
          <div>
            <div className={label}>平台</div>
            <select className={field} value={form.platform} onChange={(e) => set("platform")(e.target.value)}>
              {PLATFORMS.map((p) => (
                <option key={p} value={p}>{p}</option>
              ))}
            </select>
          </div>
          <div>
            <div className={label}>内容品类（用于参考）</div>
            <select className={field} value={form.category_guess} onChange={(e) => set("category_guess")(e.target.value)}>
              {CATEGORIES.map((c) => (
                <option key={c} value={c}>{c || "未分类"}</option>
              ))}
            </select>
          </div>
          <div>
            <div className={label}>视频标题</div>
            <input className={field} value={form.title} onChange={(e) => set("title")(e.target.value)} placeholder="《如何一周涨粉十万》" />
          </div>
          <div>
            <div className={label}>作者</div>
            <input className={field} value={form.author_name} onChange={(e) => set("author_name")(e.target.value)} placeholder="up 主名" />
          </div>
          <div className="md:col-span-2">
            <div className={label}>原视频链接</div>
            <input className={field} value={form.url} onChange={(e) => set("url")(e.target.value)} placeholder="https://…" />
          </div>
          <div className="md:col-span-2">
            <div className={label}>标签（逗号分隔）</div>
            <input className={field} value={form.tags} onChange={(e) => set("tags")(e.target.value)} placeholder="口播, 知识区, 涨粉" />
          </div>
          <div className="md:col-span-2">
            <div className={label}>字幕 / 旁白全文（拆解素材，建议逐句粘贴）</div>
            <textarea
              className={`${field} min-h-40 resize-y font-mono text-xs leading-relaxed`}
              value={form.subtitle_text}
              onChange={(e) => set("subtitle_text")(e.target.value)}
              placeholder="粘贴平台字幕或语音转写文本…"
            />
          </div>
          <div>
            <div className={label}>拆解模型</div>
            <select className={field} value={form.model} onChange={(e) => set("model")(e.target.value)}>
              <option value="flash">flash（快 / 默认）</option>
              <option value="pro">pro（深度，更慢）</option>
            </select>
          </div>
          <div className="flex items-end">
            <button
              onClick={run}
              disabled={busy}
              className="w-full rounded-lg bg-amber-300 px-4 py-2.5 text-sm font-semibold text-zinc-900 transition hover:bg-amber-200 disabled:cursor-not-allowed disabled:opacity-40"
            >
              {busy ? stage || "处理中…" : "建档并开始五层拆解"}
            </button>
          </div>
        </div>
        {error && <p className="mt-3 rounded-lg border border-rose-400/20 bg-rose-400/10 px-3 py-2 text-sm text-rose-300">{error}</p>}
      </div>

      {video && !result && busy && (
        <div className="mt-6 rounded-xl border border-white/5 bg-[#1c1f26] p-4 text-sm text-zinc-400">
          正在拆解「{video.title}」… <LayersRail r={{ status: "running", current_layer: 1, layers: [], id: video.id, video_id: video.id, summary: {}, meta: {} }} />
        </div>
      )}

      {result && (
        <div className="mt-6 space-y-4">
          <div className="flex items-center justify-between rounded-xl border border-white/5 bg-[#1c1f26] px-4 py-3">
            <div className="min-w-0">
              <div className="truncate text-sm font-medium text-zinc-100">{video?.title ?? result.video?.title}</div>
              <div className="text-[11px] text-zinc-500">{video?.platform} · {video?.author_name ?? "未知作者"} · {video?.created_at?.slice(0, 10)}</div>
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
