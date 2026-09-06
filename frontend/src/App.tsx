import { useEffect, useState } from "react";
import BreakdownView from "./views/BreakdownView";
import LibraryView from "./views/LibraryView";
import ElementsView from "./views/ElementsView";
import CreateView from "./views/CreateView";
import UsageView from "./views/UsageView";
import ModelsView from "./views/ModelsView";
import SettingsView from "./views/SettingsView";
import AccountsView from "./views/AccountsView";

type ViewKey = "breakdown" | "library" | "elements" | "create" | "usage" | "models" | "accounts" | "settings";

const NAV: Array<{ key: ViewKey; label: string; icon: string; hint: string }> = [
  { key: "breakdown", label: "拆解工作台", icon: "▸", hint: "新建素材并跑五层拆解" },
  { key: "library", label: "拆解库", icon: "▤", hint: "历史素材与拆解结果" },
  { key: "elements", label: "元素库", icon: "◇", hint: "跨片元素检索与质控" },
  { key: "create", label: "创作台", icon: "✎", hint: "对话式创作 · @元素库素材生成脚本" },
  { key: "usage", label: "AI 用量", icon: "◍", hint: "服务商卡片 · 用量 · 余额预警" },
  { key: "models", label: "模型管理", icon: "⌘", hint: "接入各家模型 · api-key 管理" },
  { key: "accounts", label: "采集账号", icon: "◎", hint: "平台登录态管理" },
  { key: "settings", label: "系统设置", icon: "⚙", hint: "服务与网关状态" },
];

export default function App() {
  const [view, setView] = useState<ViewKey>("breakdown");
  const [libraryFocusId, setLibraryFocusId] = useState<string | null>(null);
  const [isMobile, setIsMobile] = useState(
    () => typeof window !== "undefined" && window.matchMedia("(max-width: 767px)").matches,
  );
  useEffect(() => {
    const mq = window.matchMedia("(max-width: 767px)");
    const onChange = (e: MediaQueryListEvent) => setIsMobile(e.matches);
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);

  function openVideoInLibrary(videoId: string) {
    setLibraryFocusId(videoId);
    setView("library");
  }

  const main = (
    <main className="min-h-0 flex-1 overflow-y-auto">
      {view === "breakdown" && <BreakdownView />}
      {view === "library" && (
        <LibraryView
          focusVideoId={libraryFocusId}
          onFocusConsumed={() => setLibraryFocusId(null)}
        />
      )}
      {view === "elements" && <ElementsView onOpenInLibrary={openVideoInLibrary} />}
      {view === "create" && <CreateView />}
      {view === "usage" && <UsageView />}
      {view === "models" && <ModelsView />}
      {view === "accounts" && <AccountsView />}
      {view === "settings" && <SettingsView />}
    </main>
  );

  if (isMobile) {
    return (
      <div className="flex h-screen flex-col bg-[#14161a] text-zinc-200">
        <header className="shrink-0 border-b border-white/5 bg-[#0f1114] px-3 pb-2 pt-3">
          <div className="mb-2 flex items-center gap-2">
            <span className="text-lg font-semibold tracking-wide text-amber-300">帧间</span>
            <span className="text-[10px] text-zinc-500">爆款拆解 · 元素飞轮</span>
          </div>
          <nav className="no-scrollbar -mx-1 flex gap-1 overflow-x-auto px-1">
            {NAV.map((n) => (
              <button
                key={n.key}
                onClick={() => setView(n.key)}
                className={`flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-full px-3 py-1.5 text-xs transition-colors ${
                  view === n.key
                    ? "bg-amber-300/15 text-amber-200"
                    : "text-zinc-400 hover:bg-white/5 hover:text-zinc-200"
                }`}
              >
                <span>{n.icon}</span>
                <span>{n.label}</span>
              </button>
            ))}
          </nav>
        </header>
        {main}
      </div>
    );
  }

  return (
    <div className="flex h-screen bg-[#14161a] text-zinc-200">
      {/* 侧栏 */}
      <aside className="flex w-56 shrink-0 flex-col border-r border-white/5 bg-[#0f1114] px-3 py-5">
        <div className="mb-6 px-2">
          <div className="text-xl font-semibold tracking-wide text-amber-300">帧间</div>
          <div className="mt-0.5 text-[11px] text-zinc-500">爆款拆解 · 元素飞轮</div>
        </div>
        <nav className="flex flex-1 flex-col gap-1">
          {NAV.map((n) => (
            <button
              key={n.key}
              onClick={() => setView(n.key)}
              className={`flex items-center gap-3 rounded-lg px-3 py-2.5 text-left text-sm transition-colors ${
                view === n.key
                  ? "bg-amber-300/10 text-amber-200"
                  : "text-zinc-400 hover:bg-white/5 hover:text-zinc-200"
              }`}
            >
              <span className="w-4 text-center">{n.icon}</span>
              <span className="flex-1">{n.label}</span>
            </button>
          ))}
        </nav>
        <div className="rounded-lg bg-white/5 px-3 py-2 text-[11px] leading-relaxed text-zinc-500">
          v0.3 · 拆解主链路已通
          <br />
          素材：手动粘贴字幕（v1）
        </div>
      </aside>

      {/* 主区 */}
      {main}
    </div>
  );
}
