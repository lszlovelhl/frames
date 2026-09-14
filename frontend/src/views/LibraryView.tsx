import { useEffect, useMemo, useRef, useState } from "react";
import { api, type AnalysisResult, type VideoItem } from "../api";
import VideoBreakdown from "../components/VideoBreakdown";
import { LayerBadge, LayersRail } from "../components/ResultPanels";

interface Props {
  focusVideoId?: string | null;
  onFocusConsumed?: () => void;
  onCreateWithElements?: (elementIds: string[]) => void;
}

const PLATFORM_LABEL: Record<string, string> = {
  bilibili: "哔哩哔哩",
  douyin: "抖音",
  youtube: "YouTube",
  xiaohongshu: "小红书",
  wechat: "微信视频号",
  demo: "演示",
};
const PLATFORM_ORDER = ["哔哩哔哩", "抖音", "YouTube", "小红书", "微信视频号", "演示"];
const CATEGORY_ORDER = [
  "知识口播", "剧情短剧", "美食", "搞笑", "美妆", "萌宠", "游戏", "音乐舞蹈", "运动健身", "情感",
  "生活记录", "科技数码", "财经职场", "汽车出行", "文旅非遗", "综艺娱乐", "亲子育儿", "影视解说", "时尚穿搭", "好物测评",
];
const UNKNOWN_CATEGORY = "未分类";
const GROUP_STORE_KEY = "frames:library:collapsedGroups";
const STATUS_OPTIONS = [
  { value: "all", label: "全部状态" },
  { value: "done", label: "已完成" },
  { value: "partial", label: "部分完成" },
  { value: "running", label: "进行中" },
  { value: "failed", label: "失败" },
  { value: "none", label: "未拆解" },
];

export default function LibraryView({ focusVideoId, onFocusConsumed, onCreateWithElements }: Props) {
  const [videos, setVideos] = useState<VideoItem[]>([]);
  const [selected, setSelected] = useState<VideoItem | null>(null);
  const [result, setResult] = useState<AnalysisResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  // 视频列表列可收窄为竖条：rail=true 后鼠标移入展开、移出折叠；点击可固定展开
  const [rail, setRail] = useState(false);
  const [hoverOpen, setHoverOpen] = useState(false);
  const [isWide, setIsWide] = useState(
    () => typeof window !== "undefined" && window.matchMedia("(min-width: 1024px)").matches,
  );
  useEffect(() => {
    const mq = window.matchMedia("(min-width: 1024px)");
    const onChange = (e: MediaQueryListEvent) => setIsWide(e.matches);
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);

  const railOpen = !rail || hoverOpen || !isWide;

  // 搜索与筛选
  const [keyword, setKeyword] = useState("");
  const [platformFilter, setPlatformFilter] = useState("all");
  const [statusFilter, setStatusFilter] = useState("all");
  const [categoryFilter, setCategoryFilter] = useState("all");
  // 分组维度：platform=按平台 / category=按内容赛道
  const [groupBy, setGroupBy] = useState<"platform" | "category">(() => {
    try {
      const raw = localStorage.getItem("frames:library:groupBy");
      if (raw === "category" || raw === "platform") return raw;
    } catch {
      /* ignore */
    }
    return "platform";
  });
  const persistGroupBy = (g: "platform" | "category") => {
    try {
      localStorage.setItem("frames:library:groupBy", g);
    } catch {
      /* ignore */
    }
  };

  // 分组折叠记忆：读 localStorage；无记忆时（首次进入）跟随当前打开的平台，默认收起其它平台
  const userAdjusted = useRef(false);
  const [collapsedGroups, setCollapsedGroups] = useState<string[]>(() => {
    try {
      const raw = localStorage.getItem(`${GROUP_STORE_KEY}:${groupBy}`);
      if (raw) {
        userAdjusted.current = true;
        return JSON.parse(raw) as string[];
      }
    } catch {
      /* ignore */
    }
    return [];
  });

  const persistGroups = (list: string[]) => {
    try {
      localStorage.setItem(`${GROUP_STORE_KEY}:${groupBy}`, JSON.stringify(list));
    } catch {
      /* ignore */
    }
  };

  const labelOf = (v: VideoItem) => PLATFORM_LABEL[v.platform] || v.platform || "其他";
  const groupKeyOf = (v: VideoItem) => (groupBy === "platform" ? labelOf(v) : v.category_guess || UNKNOWN_CATEGORY);

  const categoryOptions = useMemo(() => {
    const seen = new Map<string, string>();
    for (const v of videos) {
      const key = v.category_guess || UNKNOWN_CATEGORY;
      if (!seen.has(key)) seen.set(key, v.category_guess || UNKNOWN_CATEGORY);
    }
    const rank = (k: string) => {
      const i = CATEGORY_ORDER.indexOf(k);
      if (k === UNKNOWN_CATEGORY) return 998;
      return i < 0 ? 997 : i;
    };
    return [...seen.entries()].sort((a, b) => rank(a[0]) - rank(b[0]));
  }, [videos]);

  const platformOptions = useMemo(() => {
    const seen = new Map<string, string>();
    for (const v of videos) {
      const key = v.platform || "other";
      if (!seen.has(key)) seen.set(key, labelOf(v));
    }
    const rank = (k: string) => {
      const i = PLATFORM_ORDER.indexOf(seen.get(k) || k);
      return i < 0 ? 99 : i;
    };
    return [...seen.entries()].sort((a, b) => rank(a[0]) - rank(b[0]));
  }, [videos]);

  const filteredVideos = useMemo(() => {
    const kw = keyword.trim().toLowerCase();
    return videos.filter((v) => {
      if (platformFilter !== "all" && (v.platform || "") !== platformFilter) return false;
      if (categoryFilter !== "all") {
        const c = v.category_guess || UNKNOWN_CATEGORY;
        if (c !== categoryFilter) return false;
      }
      if (statusFilter === "none") {
        if (v.latest_analysis) return false;
      } else if (statusFilter !== "all") {
        if (v.latest_analysis?.status !== statusFilter) return false;
      }
      if (kw) {
        const hay = `${v.title} ${v.author_name ?? ""} ${v.category_guess ?? ""}`.toLowerCase();
        if (!hay.includes(kw)) return false;
      }
      return true;
    });
  }, [videos, keyword, platformFilter, categoryFilter, statusFilter]);

  const groups = useMemo(() => {
    const m = new Map<string, VideoItem[]>();
    for (const v of filteredVideos) {
      const key = groupKeyOf(v);
      if (!m.has(key)) m.set(key, []);
      m.get(key)!.push(v);
    }
    const rank = (k: string) => {
      if (groupBy === "category") {
        if (k === UNKNOWN_CATEGORY) return 998;
        const i = CATEGORY_ORDER.indexOf(k);
        return i < 0 ? 997 : i;
      }
      const i = PLATFORM_ORDER.indexOf(k);
      return i < 0 ? 99 : i;
    };
    return [...m.entries()]
      .sort((a, b) => rank(a[0]) - rank(b[0]))
      .map(([label, list]) => ({
        label,
        list: [...list].sort((x, y) => y.created_at.localeCompare(x.created_at)),
      }));
  }, [filteredVideos, groupBy, groupKeyOf]);

  // 无历史记忆且用户未手动折叠过时：打开某个视频 → 只展开其所属组，其余组默认收起
  const collapseOtherGroups = (label: string) => {
    if (userAdjusted.current) return;
    const others = [...new Set(videos.map((v) => groupKeyOf(v)).filter((l) => l !== label))];
    setCollapsedGroups((prev) => {
      if (prev.length === others.length && others.every((x) => prev.includes(x))) return prev;
      return others;
    });
  };

  const toggleGroup = (label: string) => {
    userAdjusted.current = true;
    setCollapsedGroups((prev) => {
      const next = prev.includes(label) ? prev.filter((g) => g !== label) : [...prev, label];
      persistGroups(next);
      return next;
    });
  };

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
    collapseOtherGroups(groupKeyOf(v));
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
    collapseOtherGroups(groupKeyOf(v));
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
    <div className="mx-auto max-w-[1600px] px-8 py-8">
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

      <div className="mb-4 flex flex-wrap items-center gap-2">
        <div className="relative min-w-[200px] flex-1">
          <span className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-zinc-600">⌕</span>
          <input
            value={keyword}
            onChange={(e) => setKeyword(e.target.value)}
            placeholder="搜索标题 / 作者 / 类目…"
            className="w-full rounded-lg border border-white/10 bg-[#1c1f26] py-1.5 pl-8 pr-3 text-sm text-zinc-200 placeholder:text-zinc-600 focus:border-amber-300/40 focus:outline-none"
          />
        </div>
        <select
          value={platformFilter}
          onChange={(e) => setPlatformFilter(e.target.value)}
          className="rounded-lg border border-white/10 bg-[#1c1f26] px-2.5 py-1.5 text-sm text-zinc-300 focus:border-amber-300/40 focus:outline-none"
        >
          <option value="all">全部平台</option>
          {platformOptions.map(([key, label]) => (
            <option key={key} value={key}>
              {label}
            </option>
          ))}
        </select>
        <select
          value={categoryFilter}
          onChange={(e) => setCategoryFilter(e.target.value)}
          className="rounded-lg border border-white/10 bg-[#1c1f26] px-2.5 py-1.5 text-sm text-zinc-300 focus:border-amber-300/40 focus:outline-none"
        >
          <option value="all">全部赛道</option>
          {categoryOptions.map(([key, label]) => (
            <option key={key} value={key}>
              {label}
            </option>
          ))}
        </select>
        <div className="flex overflow-hidden rounded-lg border border-white/10 text-xs">
          {(
            [
              ["platform", "按平台"],
              ["category", "按赛道"],
            ] as const
          ).map(([key, label]) => (
            <button
              key={key}
              onClick={() => {
                if (groupBy === key) return;
                setGroupBy(key);
                persistGroupBy(key);
                userAdjusted.current = true;
                try {
                  const raw = localStorage.getItem(`${GROUP_STORE_KEY}:${key}`);
                  setCollapsedGroups(raw ? (JSON.parse(raw) as string[]) : []);
                } catch {
                  setCollapsedGroups([]);
                }
              }}
              className={`px-2.5 py-1.5 transition ${
                groupBy === key ? "bg-amber-300/15 text-amber-200" : "bg-[#1c1f26] text-zinc-400 hover:bg-white/5"
              }`}
            >
              {label}
            </button>
          ))}
        </div>
        <select
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value)}
          className="rounded-lg border border-white/10 bg-[#1c1f26] px-2.5 py-1.5 text-sm text-zinc-300 focus:border-amber-300/40 focus:outline-none"
        >
          {STATUS_OPTIONS.map((s) => (
            <option key={s.value} value={s.value}>
              {s.label}
            </option>
          ))}
        </select>
        {(keyword.trim() || platformFilter !== "all" || categoryFilter !== "all" || statusFilter !== "all") && (
          <button
            onClick={() => {
              setKeyword("");
              setPlatformFilter("all");
              setCategoryFilter("all");
              setStatusFilter("all");
            }}
            className="rounded-lg border border-white/10 px-2.5 py-1.5 text-xs text-zinc-400 hover:bg-white/5 hover:text-zinc-200"
          >
            清除筛选
          </button>
        )}
        <span className="ml-1 text-xs text-zinc-600">
          {filteredVideos.length} / {videos.length} 条
        </span>
      </div>

      <div
        className="grid items-start gap-6"
        style={{
          gridTemplateColumns: isWide
            ? railOpen
              ? "300px minmax(0, 1fr)"
              : "56px minmax(0, 1fr)"
            : "1fr",
          transition: "grid-template-columns 220ms ease",
        }}
      >
        {/* 左侧：素材列表（按平台分组，可收窄为竖条） */}
        <div
          className="min-w-0"
          onMouseEnter={() => isWide && rail && setHoverOpen(true)}
          onMouseLeave={() => isWide && rail && setHoverOpen(false)}
        >
          {rail && !railOpen ? (
            <button
              onClick={() => setRail(false)}
              title="点击固定展开 / 鼠标移入临时展开"
              className="flex min-h-[420px] w-full flex-col items-center gap-3 rounded-xl border border-white/5 bg-[#1c1f26] px-2 py-3 text-zinc-400 transition-colors hover:bg-[#232733] hover:text-zinc-200"
            >
              <span className="text-amber-300/80">▤</span>
              <span className="text-lg opacity-40">┆</span>
              <span className="whitespace-nowrap text-[10px] [writing-mode:vertical-rl] tracking-widest">素材库</span>
              {selected?.title && (
                <span className="line-clamp-4 whitespace-pre-wrap text-[9px] leading-4 text-zinc-500 [writing-mode:vertical-rl]">{selected.title}</span>
              )}
              <span className="mt-auto whitespace-nowrap text-[9px] text-zinc-600 [writing-mode:vertical-rl]">{filteredVideos.length} 条 · hover 展开</span>
            </button>
          ) : (
            <div className="flex flex-col gap-2">
              <div className="mb-1 flex items-center justify-between pr-1">
                <span className="text-[11px] uppercase tracking-wider text-zinc-600">素材列表</span>
                {isWide && (
                  <button
                    onClick={() => setRail(true)}
                    title="收窄为竖条，鼠标移入展开"
                    className="rounded border border-white/10 px-1.5 py-0.5 text-[10px] text-zinc-500 hover:bg-white/5 hover:text-zinc-200"
                  >
                    收窄 ◂
                  </button>
                )}
              </div>
              {loading && <div className="text-sm text-zinc-500">加载中…</div>}
              {!loading && videos.length === 0 && (
                <div className="rounded-xl border border-dashed border-white/10 p-6 text-center text-sm text-zinc-500">还没有拆解记录，去工作台建一条吧</div>
              )}
              {!loading && videos.length > 0 && filteredVideos.length === 0 && (
                <div className="rounded-xl border border-dashed border-white/10 p-6 text-center text-sm text-zinc-500">没有匹配的素材，试试调整筛选条件</div>
              )}
              {groups.map((g) => (
                <div key={g.label} className="flex flex-col gap-1.5">
                  <button
                    onClick={() => toggleGroup(g.label)}
                    className="group flex items-center justify-between rounded-lg border border-transparent px-1.5 py-1 text-left hover:bg-white/[0.03]"
                  >
                    <span className="flex items-center gap-1.5 text-[11px] font-medium text-zinc-400">
                      <span className="text-zinc-600 transition group-hover:text-zinc-300">
                        {collapsedGroups.includes(g.label) ? "▸" : "▾"}
                      </span>
                      {g.label}
                    </span>
                    <span className="rounded bg-white/5 px-1.5 py-px text-[10px] text-zinc-500">{g.list.length}</span>
                  </button>
                  {!collapsedGroups.includes(g.label) &&
                    g.list.map((v) => (
                      <button
                        key={v.id}
                        onClick={() => void openAnalysis(v)}
                        className={`rounded-xl border p-3 text-left transition ${
                          selected?.id === v.id ? "border-amber-300/40 bg-amber-300/5" : "border-white/5 bg-[#1c1f26] hover:bg-white/[0.04]"
                        }`}
                      >
                        <div className="flex items-center gap-2">
                          <span className="rounded bg-white/5 px-1.5 py-0.5 text-[10px] uppercase text-zinc-400">{v.platform}</span>
                          {v.category_guess && (
                            <span className="rounded bg-amber-300/10 px-1.5 py-0.5 text-[10px] text-amber-200/90">{v.category_guess}</span>
                          )}
                          {v.latest_analysis && <LayerBadge status={v.latest_analysis.status} />}
                        </div>
                        <div className="mt-1.5 line-clamp-2 text-sm font-medium text-zinc-200">{v.title}</div>
                        <div className="mt-1 text-[11px] text-zinc-500">
                          {v.author_name ?? "未知作者"} · {v.created_at.slice(0, 10)}
                        </div>
                      </button>
                    ))}
                </div>
              ))}
            </div>
          )}
        </div>

        {/* 右侧：详情 */}
        <div className="min-w-0">
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
              <VideoBreakdown video={selected!} result={result} onCreateWithElements={onCreateWithElements} />
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
