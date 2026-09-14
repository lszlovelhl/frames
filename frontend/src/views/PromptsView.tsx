import { useCallback, useEffect, useMemo, useState } from "react";
import {
  api,
  type PromptCodeSummary,
  type PromptDetail,
  type PromptVersionItem,
} from "../api";

const LAYER_LABEL: Record<number, string> = {
  1: "L1 顶层预判",
  2: "L2 画面快照",
  3: "L3 结构拆解",
  4: "L4 内容精修",
  5: "L5 元素提炼",
};

function fmtTime(iso: string | null): string {
  if (!iso) return "-";
  const d = new Date(iso);
  return `${d.getMonth() + 1}-${String(d.getDate()).padStart(2, "0")} ${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

const statusBadge = (status: string) => {
  const map: Record<string, string> = {
    active: "bg-emerald-400/15 text-emerald-300",
    draft: "bg-amber-300/15 text-amber-200",
    archived: "bg-white/5 text-zinc-500",
  };
  const label: Record<string, string> = { active: "线上", draft: "草稿", archived: "已归档" };
  return (
    <span className={`rounded-full px-2 py-0.5 text-[10px] ${map[status] ?? "bg-white/5 text-zinc-400"}`}>
      {label[status] ?? status}
    </span>
  );
};

/** 筛选维度 chip 的高亮配色 */
const SCOPE_CHIP_CLS: Record<string, string> = {
  role: "bg-sky-400/20 text-sky-200",
  platform: "bg-violet-400/20 text-violet-200",
  group: "bg-amber-300/20 text-amber-200",
};

export default function PromptsView() {
  const [items, setItems] = useState<PromptCodeSummary[]>([]);
  const [selectedCode, setSelectedCode] = useState<string | null>(null);
  const [detail, setDetail] = useState<PromptDetail | null>(null);
  const [editing, setEditing] = useState<PromptVersionItem | null>(null);
  const [draftContent, setDraftContent] = useState("");
  const [draftName, setDraftName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [ok, setOk] = useState("");
  const [scopeFilter, setScopeFilter] = useState<string | null>(null);

  const loadList = useCallback(async () => {
    try {
      const r = await api.listPrompts();
      setItems(r.items);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  const loadDetail = useCallback(
    async (code: string) => {
      setSelectedCode(code);
      setDetail(null);
      setEditing(null);
      setError("");
      setOk("");
      try {
        const d = await api.getPromptDetail(code);
        setDetail(d);
        const active = d.versions.find((v) => v.status === "active") ?? d.versions[0];
        setEditing(active ?? null);
        setDraftContent(active?.content ?? "");
        setDraftName(active?.name ?? "");
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      }
    },
    [],
  );

  useEffect(() => {
    void loadList();
  }, [loadList]);

  useEffect(() => {
    if (!selectedCode && items.length > 0) void loadDetail(items[0].code);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [items]);

  const selected = detail ?? null;
  const editingVersion = editing;

  async function saveVersion(activate: boolean) {
    if (!selectedCode || !editingVersion) return;
    if (!draftContent.trim()) {
      setError("提示词内容不能为空");
      return;
    }
    setBusy(true);
    setError("");
    setOk("");
    try {
      await api.savePromptVersion(selectedCode, {
        content: draftContent,
        name: draftName.trim() || undefined,
        activate,
      });
      await loadList();
      await loadDetail(selectedCode);
      setOk(activate ? "已发布为新版本并生效" : "已保存为草稿（不影响线上）");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function activateVersion(v: PromptVersionItem) {
    if (!selectedCode || v.status === "active") return;
    setBusy(true);
    setError("");
    setOk("");
    try {
      await api.activatePromptVersion(selectedCode, v.version);
      await loadList();
      await loadDetail(selectedCode);
      setOk(`已切换线上版本到 v${v.version}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  function pickEdit(v: PromptVersionItem) {
    setEditing(v);
    setDraftContent(v.content);
    setDraftName(v.name);
    setError("");
    setOk("");
  }

  const scopeOptions = useMemo(() => {
    const opts = new Set<string>();
    for (const it of items) {
      for (const r of it.role_scope ?? []) opts.add(`role:${r}`);
      for (const p of it.platform_scope ?? []) opts.add(`platform:${p}`);
    }
    return Array.from(opts).sort();
  }, [items]);

  const grouped = useMemo(() => {
    const layers = items.filter((i) => i.layer !== null);
    const creation = items.filter((i) => i.layer === null);
    return { layers, creation };
  }, [items]);

  return (
    <div className="flex h-full min-h-0">
      {/* 左侧 code 列表 */}
      <aside className="flex w-72 shrink-0 flex-col border-r border-white/5">
        <div className="border-b border-white/5 px-4 py-3">
          <div className="text-sm font-semibold text-zinc-200">提示词模板</div>
          <div className="mt-0.5 text-[11px] text-zinc-500">分层拆解 · 创作链路 · 版本热生效</div>
        </div>
        {scopeOptions.length > 0 && (
          <div className="flex flex-wrap gap-1 border-b border-white/5 px-3 py-2">
            <button
              onClick={() => setScopeFilter(null)}
              className={`rounded-full px-2 py-0.5 text-[10px] transition-colors ${
                scopeFilter === null ? "bg-amber-300/20 text-amber-200" : "text-zinc-500 hover:text-zinc-300"
              }`}
            >
              全部
            </button>
            {scopeOptions.map((o) => {
              const dim = o.startsWith("role:") ? "role" : o.startsWith("platform:") ? "platform" : "group";
              const label =
                dim === "group" ? `组·${o.slice("group:".length)}` : dim === "platform" ? `平台·${o.slice("platform:".length)}` : o.slice("role:".length);
              const active = scopeFilter === o;
              return (
                <button
                  key={o}
                  onClick={() => setScopeFilter(active ? null : o)}
                  className={`rounded-full px-2 py-0.5 text-[10px] transition-colors ${
                    active ? `${SCOPE_CHIP_CLS[dim]} ring-1 ring-current` : "text-zinc-500 hover:text-zinc-300"
                  }`}
                >
                  {label}
                </button>
              );
            })}
          </div>
        )}
        <div className="min-h-0 flex-1 overflow-y-auto p-2">
          {grouped.layers.map((it) => (
            <button
              key={it.code}
              onClick={() => void loadDetail(it.code)}
              className={`mb-1 block w-full rounded-lg px-3 py-2 text-left transition-colors ${
                selectedCode === it.code ? "bg-amber-300/10" : "hover:bg-white/5"
              }`}
            >
              <div className="flex items-center gap-2">
                <span className="text-[10px] text-zinc-500">{LAYER_LABEL[it.layer ?? 0] ?? `L${it.layer}`}</span>
                {it.active_version !== null && (
                  <span className="rounded bg-emerald-400/10 px-1.5 py-px text-[10px] text-emerald-300">
                    v{it.active_version}
                  </span>
                )}
              </div>
              <div className={`mt-0.5 truncate text-[13px] ${selectedCode === it.code ? "text-amber-200" : "text-zinc-300"}`}>
                {it.name}
              </div>
              <div className="mt-0.5 font-mono text-[10px] text-zinc-600">{it.code}</div>
            </button>
          ))}
          {grouped.creation.length > 0 && (
            <div className="mb-1 mt-3 px-3 text-[10px] uppercase tracking-wider text-zinc-600">创作链路</div>
          )}
          {grouped.creation.map((it) => (
            <button
              key={it.code}
              onClick={() => void loadDetail(it.code)}
              className={`mb-1 block w-full rounded-lg px-3 py-2 text-left transition-colors ${
                selectedCode === it.code ? "bg-amber-300/10" : "hover:bg-white/5"
              }`}
            >
              <div className="flex items-center gap-2">
                <span className="text-[10px] text-zinc-500">创作</span>
                {it.active_version !== null && (
                  <span className="rounded bg-emerald-400/10 px-1.5 py-px text-[10px] text-emerald-300">
                    v{it.active_version}
                  </span>
                )}
              </div>
              <div className={`mt-0.5 truncate text-[13px] ${selectedCode === it.code ? "text-amber-200" : "text-zinc-300"}`}>
                {it.name}
              </div>
              <div className="mt-0.5 font-mono text-[10px] text-zinc-600">{it.code}</div>
            </button>
          ))}
        </div>
      </aside>

      {/* 右侧：版本历史 + 编辑器 */}
      {selected && editingVersion ? (
        <section className="flex min-h-0 flex-1 flex-col">
          <div className="flex items-center gap-3 border-b border-white/5 px-5 py-3">
            <div>
              <div className="text-sm font-semibold text-zinc-200">{selected.name}</div>
              <div className="font-mono text-[11px] text-zinc-500">
                {selected.code} · 线上 v{selected.versions.find((v) => v.status === "active")?.version ?? "-"} · 共{" "}
                {selected.versions.length} 版
              </div>
            </div>
            <div className="ml-auto flex items-center gap-2 text-[11px] text-zinc-500">
              <span>{statusBadge(editingVersion.status)}</span>
              <span>正在编辑 v{editingVersion.version}</span>
            </div>
          </div>

          <div className="min-h-0 flex-1 overflow-y-auto p-5">
            {/* 编辑区 */}
            <div className="mb-5">
              <div className="mb-2 flex items-center gap-2 text-xs text-zinc-400">
                <span>编辑内容</span>
                <span className="text-zinc-600">— 保存将追加为新版本（不改动历史）</span>
              </div>
              <input
                value={draftName}
                onChange={(e) => setDraftName(e.target.value)}
                placeholder="模板名称（可留空继承）"
                className="mb-2 w-full rounded-lg border border-white/10 bg-[#0f1114] px-3 py-2 text-[13px] text-zinc-200 outline-none focus:border-amber-300/40"
              />
              <textarea
                value={draftContent}
                onChange={(e) => setDraftContent(e.target.value)}
                spellCheck={false}
                className="h-80 w-full resize-y rounded-lg border border-white/10 bg-[#0f1114] p-3 font-mono text-[12px] leading-relaxed text-zinc-200 outline-none focus:border-amber-300/40"
              />
              {error && <div className="mt-2 text-xs text-rose-400">{error}</div>}
              {ok && <div className="mt-2 text-xs text-emerald-400">{ok}</div>}
              <div className="mt-3 flex gap-2">
                <button
                  disabled={busy}
                  onClick={() => void saveVersion(true)}
                  className="rounded-lg bg-amber-300 px-4 py-2 text-xs font-semibold text-zinc-900 transition-opacity hover:opacity-90 disabled:opacity-40"
                >
                  {busy ? "保存中…" : "发布新版本（立即生效）"}
                </button>
                <button
                  disabled={busy}
                  onClick={() => void saveVersion(false)}
                  className="rounded-lg border border-white/10 px-4 py-2 text-xs text-zinc-300 transition-colors hover:bg-white/5 disabled:opacity-40"
                >
                  存为草稿
                </button>
              </div>
            </div>

            {/* 历史版本 */}
            <div>
              <div className="mb-2 text-xs text-zinc-400">历史版本（点击可载入编辑）</div>
              <div className="space-y-1">
                {selected.versions.map((v) => (
                  <div
                    key={v.id}
                    onClick={() => pickEdit(v)}
                    className={`flex cursor-pointer items-center gap-3 rounded-lg border px-3 py-2 transition-colors ${
                      v.id === editingVersion.id
                        ? "border-amber-300/30 bg-amber-300/5"
                        : "border-white/5 bg-white/[0.02] hover:bg-white/5"
                    }`}
                  >
                    <span className="w-10 shrink-0 font-mono text-xs text-zinc-400">v{v.version}</span>
                    {statusBadge(v.status)}
                    <span className="min-w-0 flex-1 truncate text-xs text-zinc-400">{v.name}</span>
                    <span className="shrink-0 font-mono text-[10px] text-zinc-600">{fmtTime(v.updated_at)}</span>
                    {v.status !== "active" && (
                      <button
                        disabled={busy}
                        onClick={(e) => {
                          e.stopPropagation();
                          void activateVersion(v);
                        }}
                        className="shrink-0 rounded-md border border-white/10 px-2 py-1 text-[10px] text-zinc-300 transition-colors hover:bg-white/5 disabled:opacity-40"
                      >
                        切为线上
                      </button>
                    )}
                  </div>
                ))}
              </div>
            </div>
          </div>
        </section>
      ) : (
        <div className="flex flex-1 items-center justify-center text-sm text-zinc-600">选择左侧模板开始编辑</div>
      )}
    </div>
  );
}
