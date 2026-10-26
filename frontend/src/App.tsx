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
import MemoryCenter from "./views/MemoryCenter";
import {
  IconAccounts,
  IconBrain,
  IconCollection,
  IconCreate,
  IconElements,
  IconLibrary,
  IconLogout,
  IconMessage,
  IconModel,
  IconMore,
  IconProducts,
  IconPrompt,
  IconSettings,
  IconSparkles,
  IconUsage,
} from "./components/Icons";

type ViewKey =
  | "chat"
  | "breakdown"
  | "library"
  | "elements"
  | "create"
  | "products"
  | "prompts"
  | "usage"
  | "models"
  | "accounts"
  | "settings"
  | "collection"
  | "memory";

type NavItem = { key: ViewKey; label: string; icon: (p: object) => React.ReactNode; hint: string };

const NAV_GROUPS: Array<{ group: string; items: NavItem[] }> = [
  {
    group: "创作流程",
    items: [
      { key: "chat", label: "AI 助手", icon: IconMessage, hint: "说人话就能用，发链接自动拆解" },
      { key: "breakdown", label: "新建拆解", icon: IconCreate, hint: "新建素材并跑拆解" },
      { key: "library", label: "拆解库", icon: IconLibrary, hint: "历史素材与拆解结果" },
      { key: "create", label: "创作台", icon: IconSparkles, hint: "对话式创作 · 生成脚本" },
    ],
  },
  {
    group: "素材库",
    items: [
      { key: "elements", label: "元素库", icon: IconElements, hint: "跨片元素检索与质控" },
      { key: "products", label: "产品库", icon: IconProducts, hint: "产品参数卖点 · 种草带入" },
    ],
  },
  {
    group: "记忆与系统",
    items: [
      { key: "memory", label: "记忆中心", icon: IconBrain, hint: "AI 记住了你什么 · 可删可改" },
      { key: "collection", label: "自动采集", icon: IconCollection, hint: "热榜/关键词自动采集" },
      { key: "prompts", label: "提示词", icon: IconPrompt, hint: "拆解/创作提示词模板" },
      { key: "usage", label: "AI 用量", icon: IconUsage, hint: "服务商 · 用量 · 余额预警" },
      { key: "models", label: "模型管理", icon: IconModel, hint: "接入各家模型 · api-key" },
      { key: "accounts", label: "采集账号", icon: IconAccounts, hint: "平台登录态管理" },
      { key: "settings", label: "系统设置", icon: IconSettings, hint: "服务与网关状态" },
    ],
  },
];

// 移动端底部悬浮 Tab Bar：5 个主入口，中间"新建拆解"突出
const MOBILE_TABS: Array<{ key: ViewKey | "__more"; label: string; icon: (p: object) => React.ReactNode; hint: string }> = [
  NAV_GROUPS[0].items[0], // AI 助手
  NAV_GROUPS[0].items[2], // 拆解库
  NAV_GROUPS[0].items[1], // 新建拆解（中间）
  NAV_GROUPS[0].items[3], // 创作台
  { key: "__more", label: "更多", icon: IconMore, hint: "" },
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
  const [view, setView] = useState<ViewKey>("chat");
  const [moreOpen, setMoreOpen] = useState(false);
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

  const go = (k: ViewKey) => {
    setView(k);
    setMoreOpen(false);
  };

  const main = (
    <main key={view} className="anim-fade-up min-h-0 flex-1 overflow-y-auto">
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
      {view === "memory" && <MemoryCenter />}
      {view === "prompts" && <PromptsView />}
      {view === "usage" && <UsageView />}
      {view === "models" && <ModelsView />}
      {view === "accounts" && <AccountsView />}
      {view === "collection" && <CollectionView />}
      {view === "settings" && <SettingsView />}
    </main>
  );

  /* ---------- 移动端：顶部简洁头 + 底部悬浮 Tab Bar ---------- */
  if (isMobile) {
    const rest = [...NAV_GROUPS[0].items.slice(3), ...NAV_GROUPS[1].items, ...NAV_GROUPS[2].items];
    return (
      <div className="flex h-screen flex-col bg-[var(--bg-elevated)] text-[var(--text-1)]">
        <header className="glass flex shrink-0 items-center justify-between px-4 py-3">
          <div className="flex items-center gap-2">
            <img src="/logo.png" alt="帧间" className="h-7 w-7 rounded-[9px]" />
            <span className="text-[17px] font-semibold tracking-tight text-white">帧间</span>
          </div>
          <button
            onClick={logout}
            className="btn btn-ghost btn-sm"
            aria-label="退出登录"
          >
            退出
          </button>
        </header>

        {main}
        <JobMonitor onOpenVideo={openVideoInLibrary} />

        {/* 底部悬浮 Tab Bar（iOS 风格，毛玻璃） */}
        <nav className="glass fixed bottom-3 left-3 right-3 z-50 flex items-center justify-around rounded-[26px] px-2 py-1.5 shadow-[var(--shadow-float)]">
          {MOBILE_TABS.map((n) => {
            const active = n.key === "__more" ? moreOpen : view === n.key;
            const Icon = n.icon as (p: object) => React.ReactNode;
            const isCenter = n.key === "breakdown";
            return (
              <button
                key={n.key}
                onClick={() => (n.key === "__more" ? setMoreOpen((v) => !v) : go(n.key as ViewKey))}
                className={`relative flex flex-col items-center gap-0.5 rounded-2xl px-3 py-1.5 transition-all ${
                  isCenter
                    ? "-mt-7 h-12 w-12 items-center justify-center rounded-full bg-[var(--accent)] text-white shadow-[0_6px_16px_rgba(10,132,255,0.4)]"
                    : active
                      ? "text-[var(--accent)]"
                      : "text-[var(--text-2)]"
                }`}
              >
                <span className={`text-[19px] ${active && !isCenter ? "anim-pulse" : ""}`}>
                  <Icon />
                </span>
                {!isCenter && <span className="text-[9.5px] font-medium">{n.label}</span>}
              </button>
            );
          })}
        </nav>

        {/* 更多抽屉：其余全部入口 + 记忆中心 */}
        {moreOpen && (
          <div
            className="anim-fade-in fixed inset-0 z-40 bg-black/50"
            onClick={() => setMoreOpen(false)}
          >
            <div
              className="anim-fade-up absolute bottom-20 left-3 right-3 rounded-[24px] bg-[var(--bg-card)] p-3 shadow-[var(--shadow-float)]"
              onClick={(e) => e.stopPropagation()}
            >
              <div className="grid grid-cols-4 gap-1">
                {rest.map((n) => {
                  const Icon = n.icon as (p: object) => React.ReactNode;
                  return (
                    <button
                      key={n.key}
                      onClick={() => go(n.key)}
                      className="flex flex-col items-center gap-1 rounded-2xl px-2 py-3 text-[var(--text-2)] transition-colors hover:bg-[var(--bg-hover)] hover:text-[var(--text-1)]"
                    >
                      <span className="text-[20px]"><Icon /></span>
                      <span className="text-[10px]">{n.label}</span>
                    </button>
                  );
                })}
              </div>
            </div>
          </div>
        )}
      </div>
    );
  }

  /* ---------- 桌面：苹果风侧栏 ---------- */
  return (
    <div className="flex h-screen bg-[var(--bg-elevated)] text-[var(--text-1)]">
      <aside className="flex w-60 shrink-0 flex-col border-r border-[var(--separator-soft)] bg-[var(--bg-base)] px-3 py-4">
        <div className="mb-6 flex items-center gap-2.5 px-2">
          <img src="/logo.png" alt="帧间" className="h-9 w-9 rounded-[10px]" />
          <div>
            <div className="text-[17px] font-semibold tracking-tight text-white">帧间</div>
            <div className="mt-0.5 text-[11px] text-[var(--text-2)]">爆款拆解 · AI 原生化</div>
          </div>
        </div>

        <nav className="flex flex-1 flex-col gap-5 overflow-y-auto pr-1">
          {NAV_GROUPS.map((group) => (
            <div key={group.group}>
              <div className="mb-1 px-3 text-[10px] font-medium uppercase tracking-[0.08em] text-[var(--text-3)]">
                {group.group}
              </div>
              <div className="flex flex-col gap-0.5">
                {group.items.map((n) => {
                  const active = view === n.key;
                  const Icon = n.icon as (p: object) => React.ReactNode;
                  return (
                    <button
                      key={n.key}
                      onClick={() => go(n.key)}
                      title={n.hint}
                      className={`group flex items-center gap-2.5 rounded-[10px] px-3 py-[7px] text-[13px] transition-all duration-150 ${
                        active
                          ? "bg-[var(--bg-selected)] font-medium text-[var(--accent)]"
                          : "text-[var(--text-2)] hover:bg-[var(--bg-hover)] hover:text-[var(--text-1)]"
                      }`}
                    >
                      <span className={`text-[17px] ${active ? "text-[var(--accent)]" : "text-[var(--text-3)] group-hover:text-[var(--text-2)]"}`}>
                        <Icon />
                      </span>
                      <span>{n.label}</span>
                      {active && <span className="ml-auto h-1.5 w-1.5 rounded-full bg-[var(--accent)]" />}
                    </button>
                  );
                })}
              </div>
            </div>
          ))}
        </nav>

        <div className="mt-3 rounded-[14px] border border-[var(--separator-soft)] bg-[var(--bg-card)] px-3 py-2 text-[11px] leading-relaxed text-[var(--text-3)]">
          v0.4 · 记忆本地 · 不上云
          <br />
          AI 拆解 · 自动沉淀 · 自我反思
        </div>
        <div className="mt-2.5 flex items-center justify-between rounded-[14px] bg-[var(--bg-card)] px-3 py-2">
          <div className="min-w-0">
            <div className="truncate text-[12.5px] font-medium text-white">{username || "用户"}</div>
            <div className="text-[10.5px] text-[var(--text-3)]">记忆存本地 · 不上云</div>
          </div>
          <button
            onClick={logout}
            className="btn btn-ghost btn-icon"
            title="退出登录"
            aria-label="退出登录"
          >
            <IconLogout />
          </button>
        </div>
      </aside>

      {main}
      <JobMonitor onOpenVideo={openVideoInLibrary} />
    </div>
  );
}
