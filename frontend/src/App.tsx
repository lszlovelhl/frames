import { useEffect, useState } from "react";
import { AuthProvider, useAuth } from "./auth";
import LoginView from "./views/LoginView";
import JobMonitor from "./components/JobMonitor";
import BreakdownView from "./views/BreakdownView";
import ChatView from "./views/ChatView";
import LibraryView from "./views/LibraryView";
import ElementsView from "./views/ElementsView";
import CreateView from "./views/CreateView";
import UsageView from "./views/UsageView";
import ModelsView from "./views/ModelsView";
import SettingsView from "./views/SettingsView";
import AccountsView from "./views/AccountsView";
import PromptsView from "./views/PromptsView";
import ProductsView from "./views/ProductsView";
import CollectionView from "./views/CollectionView";

type ViewKey = "chat" | "breakdown" | "library" | "elements" | "create" | "products" | "prompts" | "usage" | "models" | "accounts" | "settings" | "collection";

const NAV_GROUPS: Array<{ group: string; items: Array<{ key: ViewKey; label: string; icon: string; hint: string }> }> = [
  {
    group: "创作流程",
    items: [
      { key: "chat", label: "AI 助手", icon: "💬", hint: "说人话就能用，发链接自动拆解" },
      { key: "breakdown", label: "新建拆解", icon: "▸", hint: "新建素材并跑五层拆解" },
      { key: "library", label: "拆解库", icon: "▤", hint: "历史素材与拆解结果" },
      { key: "create", label: "创作台", icon: "✎", hint: "对话式创作 · @元素/产品生成脚本" },
    ],
  },
  {
    group: "素材库",
    items: [
      { key: "elements", label: "元素库", icon: "◇", hint: "跨片元素检索与质控" },
      { key: "products", label: "产品库", icon: "◈", hint: "主流产品参数卖点 · 创作带入种草" },
    ],
  },
  {
    group: "系统管理",
    items: [
      { key: "collection", label: "自动采集", icon: "⟳", hint: "热榜/关键词自动采集热门视频" },
      { key: "prompts", label: "提示词", icon: "¶", hint: "分层/创作提示词模板 · 版本管理" },
      { key: "usage", label: "AI 用量", icon: "◍", hint: "服务商卡片 · 用量 · 余额预警" },
      { key: "models", label: "模型管理", icon: "⌘", hint: "接入各家模型 · api-key 管理" },
      { key: "accounts", label: "采集账号", icon: "◎", hint: "平台登录态管理" },
      { key: "settings", label: "系统设置", icon: "⚙", hint: "服务与网关状态" },
    ],
  },
];

export default function App() {
  return (
    <AuthProvider>
      <AppInner />
    </AuthProvider>
  );
}

function AppInner() {
  const { authed, username, logout } = useAuth();
  const [view, setView] = useState<ViewKey>("breakdown");
  const [libraryFocusId, setLibraryFocusId] = useState<string | null>(null);
  const [createSeed, setCreateSeed] = useState<string[] | null>(null);
  const [createSeedProducts, setCreateSeedProducts] = useState<string[] | null>(null);
  const [isMobile, setIsMobile] = useState(
    () => typeof window !== "undefined" && window.matchMedia("(max-width: 767px)").matches,
  );
  useEffect(() => {
    const mq = window.matchMedia("(max-width: 767px)");
    const onChange = (e: MediaQueryListEvent) => setIsMobile(e.matches);
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);

  if (!authed) {
    return <LoginView />;
  }

  function openVideoInLibrary(videoId: string) {
    setLibraryFocusId(videoId);
    setView("library");
  }

  function openCreateWithElements(elementIds: string[]) {
    setCreateSeedProducts(null);
    setCreateSeed(elementIds);
    setView("create");
  }

  function openCreateWithProducts(productIds: string[]) {
    setCreateSeed(null);
    setCreateSeedProducts(productIds);
    setView("create");
  }

  const main = (
    <main className="min-h-0 flex-1 overflow-y-auto">
      {view === "chat" && <ChatView />}
      {view === "breakdown" && <BreakdownView onOpenVideo={openVideoInLibrary} />}
      {view === "library" && (
        <LibraryView
          focusVideoId={libraryFocusId}
          onFocusConsumed={() => setLibraryFocusId(null)}
          onCreateWithElements={openCreateWithElements}
        />
      )}
      {view === "elements" && (
        <ElementsView
          onOpenInLibrary={openVideoInLibrary}
          onCreateWithElements={openCreateWithElements}
        />
      )}
      {view === "products" && <ProductsView onCreateWithProducts={openCreateWithProducts} />}
      {view === "create" && (
        <CreateView
          seedElementIds={createSeed ?? undefined}
          seedProductIds={createSeedProducts ?? undefined}
        />
      )}
      {view === "prompts" && <PromptsView />}
      {view === "usage" && <UsageView />}
      {view === "models" && <ModelsView />}
      {view === "accounts" && <AccountsView />}
      {view === "collection" && <CollectionView />}
      {view === "settings" && <SettingsView />}
    </main>
  );

  if (isMobile) {
    return (
      <div className="flex h-screen flex-col bg-[#1a1d23] text-zinc-200">
        <header className="shrink-0 border-b border-white/5 bg-[#1e2229] px-3 pb-2 pt-3">
          <div className="mb-2 flex items-center gap-2">
            <img src="/logo.png" alt="帧间" className="h-6 w-6 rounded-lg" />
            <span className="text-lg font-semibold tracking-wide text-amber-300">帧间</span>
            <span className="text-[10px] text-zinc-400">爆款拆解 · 元素飞轮</span>
          </div>
          <nav className="no-scrollbar -mx-1 flex gap-1 overflow-x-auto px-1">
            {NAV_GROUPS[0].items.map((n) => (
              <button
                key={n.key}
                onClick={() => setView(n.key)}
                className={`flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-full px-3 py-1.5 text-xs transition-colors ${
                  view === n.key
                    ? "bg-amber-300/15 text-amber-200"
                    : "text-zinc-300 hover:bg-white/5 hover:text-zinc-200"
                }`}
              >
                <span>{n.icon}</span>
                <span>{n.label}</span>
              </button>
            ))}
          </nav>
        </header>
        {main}
        <JobMonitor onOpenVideo={openVideoInLibrary} />
      </div>
    );
  }

  return (
    <div className="flex h-screen bg-[#1a1d23] text-zinc-200">
      {/* 侧栏 */}
      <aside className="flex w-64 shrink-0 flex-col border-r border-white/5 bg-[#1e2229] px-3 py-5">
        <div className="mb-6 px-2">
          <div className="flex items-center gap-2">
            <img src="/logo.png" alt="帧间" className="h-8 w-8 rounded-lg" />
            <div>
              <div className="text-xl font-semibold tracking-wide text-amber-300">帧间</div>
              <div className="mt-0.5 text-[11px] text-zinc-400">爆款拆解 · 元素飞轮</div>
            </div>
          </div>
        </div>
        <nav className="flex flex-1 flex-col gap-4 overflow-y-auto">
          {NAV_GROUPS.map((group) => (
            <div key={group.group}>
              <div className="mb-1.5 px-3 text-[10px] uppercase tracking-wider text-zinc-600">{group.group}</div>
              <div className="flex flex-col gap-0.5">
                {group.items.map((n) => (
                  <button
                    key={n.key}
                    onClick={() => setView(n.key)}
                    className={`flex items-center gap-3 rounded-lg px-3 py-2 text-left text-sm transition-colors ${
                      view === n.key
                        ? "bg-amber-300/10 text-amber-200"
                        : "text-zinc-300 hover:bg-white/5 hover:text-zinc-200"
                    }`}
                  >
                    <span className="w-4 text-center">{n.icon}</span>
                    <span className="flex-1">{n.label}</span>
                  </button>
                ))}
              </div>
            </div>
          ))}
        </nav>
        <div className="rounded-lg bg-white/5 px-3 py-2 text-[11px] leading-relaxed text-zinc-400">
          v0.4 · 两层拆解 + 自动采集
          <br />
          L1云端定框架 + L2本地展开成稿
        </div>
        <div className="mt-3 flex items-center justify-between rounded-lg border border-white/5 bg-white/5 px-3 py-2">
          <div className="min-w-0">
            <div className="truncate text-xs font-medium text-zinc-200">{username || "用户"}</div>
            <div className="text-[10px] text-zinc-500">记忆存本地 · 不上云</div>
          </div>
          <button
            onClick={logout}
            className="shrink-0 rounded-md px-2 py-1 text-[11px] text-zinc-400 transition-colors hover:bg-white/10 hover:text-zinc-200"
          >
            退出
          </button>
        </div>
      </aside>

      {/* 主区 */}
      {main}
      <JobMonitor onOpenVideo={openVideoInLibrary} />
    </div>
  );
}
