import { useState } from "react";
import { api, type BatchAnalyseResult } from "../api";

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

/** 从粘贴内容里切出链接：支持换行 / 空格 / 逗号 / 中英文分号混合，自动去重 */
function parseUrls(text: string): string[] {
  const parts = text
    .split(/[\s,，;；|]+/)
    .map((s) => s.trim())
    .filter(Boolean);
  const seen = new Set<string>();
  const out: string[] = [];
  for (const p of parts) {
    if (!/^https?:\/\//i.test(p)) continue;
    if (seen.has(p)) continue;
    seen.add(p);
    out.push(p);
  }
  return out;
}

export default function BreakdownView({ onOpenVideo }: { onOpenVideo?: (videoId: string) => void }) {
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [submitted, setSubmitted] = useState<BatchAnalyseResult | null>(null);

  const urls = parseUrls(url);
  const rawCount = url.split(/[\s,，;；|]+/).filter(Boolean).length;

  async function run() {
    if (!url.trim()) {
      setError("请先粘贴视频链接（可一次粘贴多条）");
      return;
    }
    if (urls.length === 0) {
      setError("未识别到合法链接，请确认链接以 http:// 或 https:// 开头");
      return;
    }
    setBusy(true);
    setError("");
    setSubmitted(null);
    try {
      // 批量提交：立即返回，进度由右下角常驻面板展示（切视图 / 切应用不丢）
      const r = await api.batchAnalyse(urls, 5, true);
      setSubmitted(r);
      setUrl("");
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
        <p className="mt-1 text-sm text-zinc-500">
          粘贴视频链接即可批量提交，系统自动抓取并多模态拆解（画面 + 语音 + 文案 + 音乐）；提交后进度在右下角常驻显示，切到别的页面或应用再回来依然可见
        </p>
      </header>

      <div className="rounded-xl border border-white/5 bg-[#1c1f26] p-6">
        <div className={field + " !border-white/15 !bg-white/[0.05] !px-4 !py-3.5"}>
          <div className="mb-1.5 flex items-center gap-2 text-xs text-zinc-400">
            <span className="text-amber-300/80">🔗</span>
            <span>视频链接（每行一条，可一次粘贴多条批量拆解）</span>
          </div>
          <textarea
            className="h-24 w-full resize-y bg-transparent text-[14px] leading-relaxed text-zinc-50 placeholder-zinc-600 outline-none"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && (e.metaKey || e.ctrlKey) && !busy) run();
            }}
            placeholder={"https://www.bilibili.com/video/BV1xx411c7mD\nhttps://www.douyin.com/video/7300000000000000000\n（支持 B站 / 抖音 / YouTube，最多 20 条一次）"}
            disabled={busy}
          />
        </div>

        <div className="mt-3 flex flex-wrap items-center gap-3">
          <button
            onClick={run}
            disabled={busy}
            className="shrink-0 rounded-lg bg-amber-300 px-6 py-2.5 text-sm font-semibold text-zinc-900 transition hover:bg-amber-200 disabled:cursor-not-allowed disabled:opacity-40"
          >
            {busy ? "提交中…" : urls.length > 1 ? `批量拆解 ${urls.length} 条` : "一键多模态拆解"}
          </button>
          <span className="text-[11px] text-zinc-500">
            {rawCount > 0
              ? `已识别 ${urls.length} 条有效链接${rawCount > urls.length ? `（${rawCount - urls.length} 条格式不符已忽略）` : ""}`
              : "⌘/Ctrl + Enter 快捷提交"}
          </span>
        </div>

        <ul className="mt-4 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-zinc-500">
          {AUTO_STEPS.map((s, i) => (
            <li key={i} className="flex items-center gap-1">
              <span className="text-amber-300/60">{i + 1}</span>
              <span>{s}</span>
            </li>
          ))}
        </ul>

        {error && <p className="mt-4 rounded-lg border border-rose-400/20 bg-rose-400/10 px-3 py-2 text-sm text-rose-300">{error}</p>}
      </div>

      {submitted && (
        <div className="mt-6 rounded-xl border border-white/5 bg-[#1c1f26] p-5">
          <div className="flex flex-wrap items-center gap-2 text-sm">
            <span className="font-medium text-emerald-300">已加入拆解队列 {submitted.queued.length} 条</span>
            {submitted.skipped.length > 0 && (
              <span className="text-[11px] text-zinc-500">{submitted.skipped.length} 条被跳过</span>
            )}
            <span className="text-[11px] text-zinc-500">进度见右下角常驻面板，完成后可在「拆解库」查看结果</span>
          </div>

          <div className="mt-3 space-y-1.5">
            {submitted.queued.map((q) => (
              <button
                key={q.analysis_id}
                onClick={() => onOpenVideo?.(q.video_id)}
                className="flex w-full items-center justify-between gap-3 rounded-lg bg-white/[0.03] px-3 py-2 text-left transition hover:bg-white/[0.06]"
              >
                <span className="truncate text-[12px] text-zinc-200">{q.title || q.url}</span>
                <span className="shrink-0 text-[10px] text-amber-300/80">排队中 · 点击查看</span>
              </button>
            ))}
          </div>

          {submitted.skipped.length > 0 && (
            <div className="mt-4">
              <div className="mb-1 text-[11px] text-zinc-500">以下链接未提交：</div>
              <ul className="space-y-1">
                {submitted.skipped.map((s, i) => (
                  <li key={i} className="truncate text-[11px] text-zinc-500">
                    {s.url} — {s.reason}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
