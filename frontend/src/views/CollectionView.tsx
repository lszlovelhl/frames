import { useEffect, useState } from "react";
import { api } from "../api";

interface CollectionConfig {
  enabled: boolean;
  hotlist_enabled: boolean;
  keyword_enabled: boolean;
  keywords: string[];
  platforms: string[];
  max_videos_per_run: number;
  min_likes: number;
  interval_minutes: number;
  last_run_at: string | null;
}

interface HotlistItem {
  id: string;
  platform: string;
  hotlist_type: string;
  title: string;
  hot_value: number;
  rank: number;
  author: string;
  summary: string;
  ai_summary?: string;
}

export default function CollectionView() {
  const [config, setConfig] = useState<CollectionConfig | null>(null);
  const [hotlist, setHotlist] = useState<HotlistItem[]>([]);
  const [aiSummary, setAiSummary] = useState<string>("");
  const [dates, setDates] = useState<string[]>([]);
  const [selectedDate, setSelectedDate] = useState<string>("");
  const [newKeyword, setNewKeyword] = useState("");
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState("");

  useEffect(() => {
    loadConfig();
    loadDates();
  }, []);

  useEffect(() => {
    if (dates.length > 0 && !selectedDate) {
      setSelectedDate(dates[0]);
      loadHotlist(dates[0]);
    }
  }, [dates]);

  async function loadConfig() {
    const data = await api.getCollectionConfig();
    setConfig(data);
  }

  async function loadDates() {
    const data = await api.getHotlistDates();
    setDates(data.dates);
  }

  async function loadHotlist(date: string) {
    const data = await api.getHotlistByDate(date);
    setHotlist(data.items);
    if (data.items && data.items.length > 0) {
      setAiSummary(data.items[0].ai_summary || "");
    } else {
      setAiSummary("");
    }
  }

  async function updateConfig(updates: Partial<CollectionConfig>) {
    if (!config) return;
    const newConfig = { ...config, ...updates };
    setConfig(newConfig);
    await api.updateCollectionConfig(updates);
    setMessage("配置已保存");
    setTimeout(() => setMessage(""), 2000);
  }

  async function addKeyword() {
    if (!config || !newKeyword.trim()) return;
    const keywords = [...config.keywords, newKeyword.trim()];
    await updateConfig({ keywords });
    setNewKeyword("");
  }

  async function removeKeyword(kw: string) {
    if (!config) return;
    const keywords = config.keywords.filter((k) => k !== kw);
    await updateConfig({ keywords });
  }

  async function refreshHotlist() {
    setLoading(true);
    try {
      const data = await api.refreshHotlist();
      setMessage(data.message);
      await loadDates();
      if (dates.length > 0) {
        await loadHotlist(dates[0]);
      }
    } catch (e: any) {
      setMessage("热榜获取失败：" + (e.message || "未知错误"));
    } finally {
      setLoading(false);
      setTimeout(() => setMessage(""), 3000);
    }
  }

  if (!config) return <div className="text-zinc-400">加载中...</div>;

  return (
    <div className="mx-auto max-w-6xl px-8 py-8">
      <header className="mb-6">
        <h1 className="text-xl font-semibold text-zinc-100">自动采集</h1>
        <p className="mt-1 text-sm text-zinc-500">
          自动从热榜/关键词获取高赞视频，下载后自动加入拆解队列
        </p>
      </header>

      {message && (
        <div className="mb-4 rounded-lg border border-amber-300/20 bg-amber-300/10 px-3 py-2 text-sm text-amber-200">
          {message}
        </div>
      )}

      {/* 采集开关 */}
      <div className="rounded-xl border border-white/5 bg-[#1c1f26] p-6 mb-4">
        <div className="flex items-center justify-between">
          <div>
            <h3 className="text-base font-semibold text-zinc-100 mb-2">自动采集总开关</h3>
            <p className="text-sm text-zinc-500">
              打开后系统会自动按配置采集热门视频并加入拆解队列
            </p>
          </div>
          <label className="flex items-center gap-3 cursor-pointer">
            <input
              type="checkbox"
              checked={config.enabled}
              onChange={(e) => updateConfig({ enabled: e.target.checked })}
              className="h-4 w-4 accent-amber-300"
            />
            <span className="text-sm text-zinc-300">{config.enabled ? "已开启" : "已关闭"}</span>
          </label>
        </div>
      </div>

      {/* 采集模式 */}
      <div className="rounded-xl border border-white/5 bg-[#1c1f26] p-6 mb-4">
        <h3 className="text-base font-semibold text-zinc-100 mb-4">采集模式</h3>

        <div className="space-y-4">
          {/* 热榜模式 */}
          <div className="flex items-center justify-between py-2">
            <div>
              <div className="text-sm font-medium text-zinc-200">热榜模式</div>
              <div className="text-xs text-zinc-500 mt-1">
                自动从B站/抖音热榜获取高赞视频
              </div>
            </div>
            <input
              type="checkbox"
              checked={config.hotlist_enabled}
              onChange={(e) => updateConfig({ hotlist_enabled: e.target.checked })}
              disabled={!config.enabled}
              className="h-4 w-4 accent-amber-300"
            />
          </div>

          {/* 关键词模式 */}
          <div className="py-2">
            <div className="flex items-center justify-between">
              <div>
                <div className="text-sm font-medium text-zinc-200">关键词模式</div>
                <div className="text-xs text-zinc-500 mt-1">
                  按指定关键词搜索热门视频
                </div>
              </div>
              <input
                type="checkbox"
                checked={config.keyword_enabled}
                onChange={(e) => updateConfig({ keyword_enabled: e.target.checked })}
                disabled={!config.enabled}
                className="h-4 w-4 accent-amber-300"
              />
            </div>

            {/* 关键词输入 */}
            {config.keyword_enabled && (
              <div className="mt-4 pl-4">
                <div className="flex gap-2 mb-3">
                  <input
                    type="text"
                    value={newKeyword}
                    onChange={(e) => setNewKeyword(e.target.value)}
                    placeholder="输入关键词（1-20字）"
                    className="flex-1 rounded-lg border border-white/10 bg-white/[0.03] px-3 py-2 text-sm text-zinc-100 placeholder-zinc-600 outline-none focus:border-amber-300/50"
                    maxLength={20}
                  />
                  <button
                    onClick={addKeyword}
                    className="rounded-lg bg-amber-300/15 px-4 py-2 text-sm font-medium text-amber-200 hover:bg-amber-300/25"
                  >
                    添加
                  </button>
                </div>
                <div className="flex flex-wrap gap-2">
                  {config.keywords.map((kw) => (
                    <span
                      key={kw}
                      className="rounded-full bg-white/5 px-3 py-1 text-xs text-zinc-300 flex items-center gap-1.5"
                    >
                      {kw}
                      <button
                        onClick={() => removeKeyword(kw)}
                        className="text-zinc-500 hover:text-zinc-300"
                      >
                        ×
                      </button>
                    </span>
                  ))}
                </div>
              </div>
            )}
          </div>
        </div>
      </div>

      {/* 采集参数 */}
      <div className="rounded-xl border border-white/5 bg-[#1c1f26] p-6 mb-4">
        <h3 className="text-base font-semibold text-zinc-100 mb-4">采集参数</h3>
        <div className="grid grid-cols-3 gap-4">
          <div>
            <label className="block text-xs text-zinc-500 mb-1.5">每次最多采集视频数</label>
            <input
              type="number"
              value={config.max_videos_per_run}
              onChange={(e) => updateConfig({ max_videos_per_run: parseInt(e.target.value) })}
              min={1}
              max={50}
              className="w-full rounded-lg border border-white/10 bg-white/[0.03] px-3 py-2 text-sm text-zinc-100 outline-none focus:border-amber-300/50"
            />
          </div>
          <div>
            <label className="block text-xs text-zinc-500 mb-1.5">最低点赞数</label>
            <input
              type="number"
              value={config.min_likes}
              onChange={(e) => updateConfig({ min_likes: parseInt(e.target.value) })}
              className="w-full rounded-lg border border-white/10 bg-white/[0.03] px-3 py-2 text-sm text-zinc-100 outline-none focus:border-amber-300/50"
            />
          </div>
          <div>
            <label className="block text-xs text-zinc-500 mb-1.5">采集间隔（分钟）</label>
            <input
              type="number"
              value={config.interval_minutes}
              onChange={(e) => updateConfig({ interval_minutes: parseInt(e.target.value) })}
              min={10}
              max={1440}
              className="w-full rounded-lg border border-white/10 bg-white/[0.03] px-3 py-2 text-sm text-zinc-100 outline-none focus:border-amber-300/50"
            />
          </div>
        </div>
      </div>

      {/* 热榜查看 */}
      <div className="rounded-xl border border-white/5 bg-[#1c1f26] p-6">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-base font-semibold text-zinc-100">每日热榜</h3>
          <button
            onClick={refreshHotlist}
            disabled={loading}
            className="rounded-lg bg-amber-300/15 px-4 py-2 text-sm font-medium text-amber-200 hover:bg-amber-300/25 disabled:opacity-50"
          >
            {loading ? "获取中..." : "刷新热榜"}
          </button>
        </div>

        {/* 日期选择 */}
        {dates.length > 0 && (
          <div className="mb-4">
            <select
              value={selectedDate}
              onChange={(e) => {
                setSelectedDate(e.target.value);
                loadHotlist(e.target.value);
              }}
              className="rounded-lg border border-white/10 bg-[#1c1f26] px-3 py-2 text-sm text-zinc-100 outline-none focus:border-amber-300/50"
            >
              {dates.map((d) => (
                <option key={d} value={d}>
                  {d}
                </option>
              ))}
            </select>
          </div>
        )}

        {/* AI热榜分析 */}
        {aiSummary && (
          <div className="mb-4 rounded-lg border border-emerald-400/20 bg-emerald-400/10 p-4">
            <h4 className="text-sm font-semibold text-emerald-300 mb-2">
              📊 AI热榜分析
            </h4>
            <div className="whitespace-pre-wrap text-sm leading-relaxed text-zinc-200">
              {aiSummary}
            </div>
          </div>
        )}

        {/* 热榜列表 - 按平台分组 */}
        {hotlist.length > 0 ? (
          <div className="space-y-6">
            {["bilibili", "douyin"].map((platform) => {
              const platformItems = hotlist.filter((item) => item.platform === platform);
              if (platformItems.length === 0) return null;
              const platformName = platform === "bilibili" ? "B站热门" : "抖音热榜";
              const platformColor = platform === "bilibili" ? "text-pink-400" : "text-cyan-400";
              return (
                <div key={platform}>
                  <h4 className={`mb-3 text-sm font-semibold ${platformColor}`}>
                    📺 {platformName}（{platformItems.length}条）
                  </h4>
                  <div className="space-y-2">
                    {platformItems.map((item) => (
                      <div
                        key={item.id}
                        className="rounded-lg border border-white/5 bg-white/[0.02] p-3"
                      >
                        <div className="flex items-center gap-3">
                          <span className="flex h-6 w-6 items-center justify-center rounded-full bg-white/5 text-xs font-semibold text-zinc-400">
                            {item.rank}
                          </span>
                          <div className="flex-1">
                            <div className="text-sm text-zinc-100">{item.title}</div>
                            <div className="mt-1 text-xs text-zinc-500">
                              {item.author} · 热度 {item.hot_value}
                            </div>
                          </div>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              );
            })}
          </div>
        ) : (
          <div className="py-12 text-center text-sm text-zinc-600">
            暂无热榜数据，点击"刷新热榜"获取
          </div>
        )}
      </div>
    </div>
  );
}
