import { useState } from "react";
import { api, type BatchAnalyseResult } from "../api";

const field =
  "w-full rounded-lg border border-[var(--separator-soft)] bg-[var(--bg-hover)] px-3 py-2 text-sm text-[var(--text-1)] placeholder-[var(--text-3)] outline-none transition focus:border-text-[var(--accent-hover)]/50";

const AUTO_STEPS = [
  "自动识别平台（B站 / 抖音 / YouTube …）",
  "下载视频并读取元数据",
  "语音转文字：全量 ASR 获取文案",
  "抽帧 + 视觉模型画面理解",
  "L1：云端大模型出框架（分段/情绪曲线/元素）",
  "L2：本地大模型展开成稿（逐句三要素+剪辑指令）",
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
    <div className="mx-auto max-w-6xl px-4 py-6 md:px-8 md:py-10">
      <header className="mb-6">
        <h1 className="text-[22px] font-semibold tracking-tight text-[var(--text-1)]">拆解工作台</h1>
        <p className="mt-1 text-sm text-[var(--text-3)]">
          粘贴视频链接即可批量提交，系统自动抓取并两层拆解（L1云端定框架 + L2本地展开成稿）；提交后进度在右下角常驻显示，切到别的页面或应用再回来依然可见
        </p>
      </header>

      <div className="rounded-xl border border-[var(--separator-soft)] bg-[var(--bg-card)] p-6">
        <div className={field + " !border-[var(--separator-soft)] !bg-[var(--bg-card-hi)] !px-4 !py-3.5"}>
          <div className="mb-1.5 flex items-center gap-2 text-xs text-[var(--text-2)]">
            <span className="text-[var(--accent-hover)]/80">🔗</span>
            <span>视频链接（每行一条，可一次粘贴多条批量拆解）</span>
          </div>
          <textarea
            className="h-24 w-full resize-y bg-transparent text-[14px] leading-relaxed text-[var(--text-1)] placeholder-[var(--text-3)] outline-none"
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
            className="shrink-0 rounded-lg bg-[var(--accent-hover)] px-6 py-2.5 text-sm font-semibold text-white transition hover:bg-text-[var(--accent-hover)] disabled:cursor-not-allowed disabled:opacity-40"
          >
            {busy ? "提交中…" : urls.length > 1 ? `批量拆解 ${urls.length} 条` : "一键多模态拆解"}
          </button>
          <span className="text-[11px] text-[var(--text-3)]">
            {rawCount > 0
              ? `已识别 ${urls.length} 条有效链接${rawCount > urls.length ? `（${rawCount - urls.length} 条格式不符已忽略）` : ""}`
              : "⌘/Ctrl + Enter 快捷提交"}
          </span>
        </div>

        <ul className="mt-4 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-[var(--text-3)]">
          {AUTO_STEPS.map((s, i) => (
            <li key={i} className="flex items-center gap-1">
              <span className="text-[var(--accent-hover)]/60">{i + 1}</span>
              <span>{s}</span>
            </li>
          ))}
        </ul>

        {error && <p className="mt-4 rounded-lg border border-rose-400/20 bg-rose-400/10 px-3 py-2 text-sm text-rose-300">{error}</p>}
      </div>

      {submitted && (
        <div className="mt-6 rounded-xl border border-[var(--separator-soft)] bg-[var(--bg-card)] p-5">
          <div className="flex flex-wrap items-center gap-2 text-sm">
            <span className="font-medium text-emerald-300">已加入拆解队列 {submitted.queued.length} 条</span>
            {submitted.skipped.length > 0 && (
              <span className="text-[11px] text-[var(--text-3)]">{submitted.skipped.length} 条被跳过</span>
            )}
            <span className="text-[11px] text-[var(--text-3)]">进度见右下角常驻面板，完成后可在「拆解库」查看结果</span>
          </div>

          <div className="mt-3 space-y-1.5">
            {submitted.queued.map((q) => (
              <button
                key={q.analysis_id}
                onClick={() => onOpenVideo?.(q.video_id)}
                className="flex w-full items-center justify-between gap-3 rounded-lg bg-[var(--bg-hover)] px-3 py-2 text-left transition hover:bg-[var(--bg-active)]"
              >
                <span className="truncate text-[12px] text-[var(--text-1)]">{q.title || q.url}</span>
                <span className="shrink-0 text-[10px] text-[var(--accent-hover)]/80">排队中 · 点击查看</span>
              </button>
            ))}
          </div>

          {submitted.skipped.length > 0 && (
            <div className="mt-4">
              <div className="mb-1 text-[11px] text-[var(--text-3)]">以下链接未提交：</div>
              <ul className="space-y-1">
                {submitted.skipped.map((s, i) => (
                  <li key={i} className="truncate text-[11px] text-[var(--text-3)]">
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
