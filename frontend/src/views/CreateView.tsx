import { useEffect, useMemo, useRef, useState } from "react";
import { api, type CreationDetail, type CreationItem, type ElementItem, type UsedElementRef } from "../api";

interface Msg {
  role: "user" | "assistant";
  content: string;
  pending?: boolean;
  saved?: boolean;
  used?: UsedElementRef[];
}

const PLATFORMS = ["不限", "抖音", "哔哩哔哩", "小红书", "视频号", "YouTube"];
const INTENTS = ["不限", "涨粉", "带货", "品牌曝光", "教学/干货", "娱乐/剧情"];

const STATUS_TAG: Record<string, string> = {
  draft: "待质控",
  accepted: "已采纳",
  adjusted: "已纠错",
  rejected: "已驳回",
};

function briefTitle(content: string, max = 36): string {
  const clean = content.replace(/^#+\s*/gm, "").replace(/\s+/g, " ").trim();
  return clean.length > max ? clean.slice(0, max) + "…" : clean || "未命名创作";
}

function fmtTime(s?: string | null): string {
  if (!s) return "";
  const d = new Date(s);
  if (Number.isNaN(d.getTime())) return s.slice(0, 16).replace("T", " ");
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

export default function CreateView() {
  const [messages, setMessages] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [platform, setPlatform] = useState("不限");
  const [intent, setIntent] = useState("不限");
  const [picked, setPicked] = useState<ElementItem[]>([]);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [pickerQ, setPickerQ] = useState("");
  const [allElements, setAllElements] = useState<ElementItem[]>([]);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [creations, setCreations] = useState<CreationItem[]>([]);
  const [activeCreation, setActiveCreation] = useState<CreationDetail | null>(null);
  const [confirmId, setConfirmId] = useState<string | null>(null);
  const [editing, setEditing] = useState<{ creationId: string; title: string; version: number; baseAssetId: string | null } | null>(null);
  const endRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLTextAreaElement | null>(null);

  useEffect(() => {
    api
      .listElements({})
      .then((d) => setAllElements(d.items ?? []))
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, []);

  useEffect(() => {
    if (drawerOpen) {
      api
        .listCreations()
        .then((d) => setCreations(d.creations ?? []))
        .catch((e) => setError(e instanceof Error ? e.message : String(e)));
    }
  }, [drawerOpen]);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages]);

  useEffect(() => {
    if (!notice) return;
    const t = setTimeout(() => setNotice(""), 2600);
    return () => clearTimeout(t);
  }, [notice]);

  const filterPicked = useMemo(() => {
    const q = pickerQ.trim().toLowerCase();
    if (!q) return allElements;
    return allElements.filter((e) => (e.name + e.category + (e.description ?? "")).toLowerCase().includes(q));
  }, [allElements, pickerQ]);

  function togglePicked(e: ElementItem) {
    setPicked((prev) => (prev.some((x) => x.id === e.id) ? prev.filter((x) => x.id !== e.id) : [...prev, e]));
  }

  async function send() {
    const text = input.trim();
    if ((!text && picked.length === 0) || sending) return;
    const userMsg: Msg = {
      role: "user",
      content: text || `请结合我选的 ${picked.length} 个元素生成一版可落地的创作方案`,
    };
    const history = [...messages.filter((m) => !m.pending), userMsg]
      .slice(-24)
      .map((m) => ({ role: m.role, content: m.content }));
    setMessages((prev) => [...prev, userMsg, { role: "assistant", content: "正在构思…", pending: true }]);
    setInput("");
    setSending(true);
    setError("");
    try {
      const r = editing
        ? await api.creationsContinueChat(editing.creationId, history, picked.map((e) => e.id))
        : await api.creationsChat(
            history,
            picked.map((e) => e.id),
            platform !== "不限" ? platform : undefined,
            intent !== "不限" ? intent : undefined
          );
      setMessages((prev) => {
        const copy = prev.slice();
        copy[copy.length - 1] = { role: "assistant", content: r.reply, used: r.used_elements ?? [] };
        return copy;
      });
      setPicked([]);
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setError(msg);
      setMessages((prev) => {
        const copy = prev.slice();
        if (copy.length && copy[copy.length - 1].pending) {
          copy[copy.length - 1] = { role: "assistant", content: `生成失败：${msg}\n\n请检查后端服务后重发一次。`, pending: false };
        }
        return copy;
      });
    } finally {
      setSending(false);
      inputRef.current?.focus();
    }
  }

  async function saveMessage(idx: number, m: Msg) {
    if (!m.content || !m.content.trim()) {
      setError("回复尚未生成完成，请稍候再保存");
      return;
    }
    const elementIds = (m.used ?? []).map((u) => u.id);
    try {
      if (editing) {
        const r = await api.saveCreationVersion(editing.creationId, {
          content: m.content,
          element_ids: elementIds,
          parent_asset_id: editing.baseAssetId,
        });
        setMessages((prev) => prev.map((x, i) => (i === idx ? { ...x, saved: true } : x)));
        setEditing((prev) =>
          prev ? { ...prev, version: r.asset.version ?? (prev.version + 1), baseAssetId: r.asset.id } : prev
        );
        setNotice(`已存为 v${r.asset.version ?? ""}`);
        if (drawerOpen) {
          const d = await api.listCreations();
          setCreations(d.creations ?? []);
        }
        return;
      }
      await api.saveCreation({
        title: briefTitle(m.content),
        content: m.content,
        element_ids: elementIds,
        platform: platform !== "不限" ? platform : null,
        intent: intent !== "不限" ? intent : null,
      });
      setMessages((prev) => prev.map((x, i) => (i === idx ? { ...x, saved: true } : x)));
      setNotice("已存入产物库");
      if (drawerOpen) {
        const d = await api.listCreations();
        setCreations(d.creations ?? []);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  async function startEdit(c: CreationItem) {
    try {
      const d = await api.getCreation(c.id);
      const latest = [...(d.assets ?? [])].filter((a) => a.asset_type === "script").pop();
      setActiveCreation(d);
      setEditing({
        creationId: c.id,
        title: d.title,
        version: latest?.version ?? d.assets.length,
        baseAssetId: null,
      });
      setDrawerOpen(false);
      setMessages([]);
      setNotice("已进入改稿模式，默认基于最新版本迭代");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  async function startEditFromAsset(a: CreationAssetView) {
    try {
      const d = await api.getCreation(activeCreation?.id ?? "");
      if (!d) return;
      setActiveCreation(d);
      setEditing({
        creationId: d.id,
        title: d.title,
        version: a.version ?? 1,
        baseAssetId: a.id,
      });
      setDrawerOpen(false);
      setMessages([]);
      setNotice(`已进入改稿模式，以 v${a.version ?? ""} 为基线`);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  async function openCreation(c: CreationItem) {
    try {
      const d = await api.getCreation(c.id);
      setActiveCreation(d);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  async function removeCreation(id: string) {
    try {
      await api.removeCreation(id);
      setActiveCreation(null);
      const d = await api.listCreations();
      setCreations(d.creations ?? []);
      setNotice("产物已删除");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
    setConfirmId(null);
  }

  function copyText(t: string) {
    navigator.clipboard?.writeText(t).then(
      () => setNotice("已复制"),
      () => setError("复制失败")
    );
  }

  return (
    <div className="flex h-full flex-col">
      {/* 顶栏 */}
      <header className="flex items-center justify-between border-b border-white/5 px-6 py-3">
        <div>
          <h1 className="text-lg font-semibold text-zinc-100">创作台</h1>
          <p className="text-xs text-zinc-500">{editing ? "改稿模式 · 针对当前版本迭代" : "对话驱动 · 可引用元素库 · 一键落卡沉淀"}</p>
        </div>
        <button onClick={() => setDrawerOpen(true)} className="rounded-lg border border-white/10 px-3 py-1.5 text-xs text-zinc-300 hover:bg-white/5">
          产物库（{creations.length}）
        </button>
      </header>

      {editing && (
        <div className="flex items-center justify-between border-b border-amber-300/15 bg-amber-300/10 px-6 py-2">
          <span className="min-w-0 truncate text-xs text-amber-200/90">
            正在改稿《{editing.title}》· v{editing.version} · 发送指令将基于当前版本迭代
          </span>
          <button
            onClick={() => {
              setEditing(null);
              setMessages([]);
              setNotice("已退出改稿模式，回到新创作");
            }}
            className="shrink-0 rounded border border-white/10 px-2 py-0.5 text-[11px] text-zinc-300 hover:text-zinc-100"
          >
            退出改稿
          </button>
        </div>
      )}

      {error && <p className="border-b border-rose-400/20 bg-rose-400/10 px-6 py-2 text-xs text-rose-300">{error}</p>}
      {notice && <p className="border-b border-emerald-400/20 bg-emerald-400/10 px-6 py-2 text-xs text-emerald-300">{notice}</p>}

      {/* 消息流 */}
      <div className="flex-1 overflow-y-auto">
        <div className="mx-auto flex w-full max-w-3xl flex-col gap-4 px-4 py-6">
          {messages.length === 0 && (
            <div className="rounded-xl border border-dashed border-white/10 p-6 text-sm leading-relaxed text-zinc-500">
              {editing ? (
                <>
                  改稿模式已开启：直接说要改哪里、怎么改，AI 会基于《{editing.title}》v{editing.version} 给出新版方案。
                  <br />
                  满意的回复点「存为新版本」，产物的每个版本都会保留下来。
                </>
              ) : (
                <>
                  和创作助手聊聊你想做的视频：选题、目标平台、想要的调性都可以直接说。
                  <br />
                  也可以先点下方「引用元素」把拆解库里提炼的爆款元素带上，AI 会基于它们生成方案。
                </>
              )}
            </div>
          )}
          {messages.map((m, i) =>
            m.role === "user" ? (
              <div key={i} className="self-end max-w-[85%] rounded-2xl rounded-br-md bg-amber-300/15 px-4 py-2.5 text-sm leading-relaxed text-amber-100/90 whitespace-pre-wrap break-words">
                {m.content}
              </div>
            ) : (
              <div key={i} className="w-full rounded-2xl border border-white/5 bg-[#1c1f26] px-4 py-3.5">
                {m.pending ? (
                  <div className="flex items-center gap-2 text-sm text-zinc-500">
                    <span className="inline-block h-3 w-3 animate-spin rounded-full border border-zinc-600 border-t-zinc-300" />
                    正在构思…
                  </div>
                ) : (
                  <>
                    <div className="whitespace-pre-wrap break-words text-sm leading-relaxed text-zinc-200">{m.content}</div>
                    {m.used && m.used.length > 0 && (
                      <div className="mt-2 flex flex-wrap gap-1">
                        {m.used.map((u) => (
                          <span key={u.id} className="rounded bg-sky-400/10 px-1.5 py-0.5 text-[10px] text-sky-300/80">
                            {u.category} · {u.name}
                          </span>
                        ))}
                      </div>
                    )}
                    <div className="mt-3 flex items-center gap-2 border-t border-white/5 pt-2.5">
                      {m.saved ? (
                        <span className="rounded px-2 py-1 text-[11px] text-emerald-300/80">
                          {editing ? "已存为新版本" : "已存产物库"}
                        </span>
                      ) : (
                        <button
                          onClick={() => void saveMessage(i, m)}
                          className="rounded border border-white/10 px-2.5 py-1 text-[11px] text-zinc-300 hover:border-emerald-300/40 hover:text-emerald-300"
                        >
                          {editing ? "存为新版本" : "落卡保存"}
                        </button>
                      )}
                      <button onClick={() => copyText(m.content)} className="rounded border border-white/10 px-2.5 py-1 text-[11px] text-zinc-400 hover:text-zinc-200">
                        复制全文
                      </button>
                    </div>
                  </>
                )}
              </div>
            )
          )}
          <div ref={endRef} />
        </div>
      </div>

      {/* 输入区 */}
      <div className="border-t border-white/5 bg-[#0f1114]">
        <div className="mx-auto flex w-full max-w-3xl flex-col gap-2 px-4 py-3">
          <div className="flex flex-wrap items-center gap-2 text-[11px]">
            {!editing && (
              <select value={platform} onChange={(e) => setPlatform(e.target.value)} className="rounded-md border border-white/10 bg-[#1c1f26] px-2 py-1 text-zinc-400 outline-none">
                {PLATFORMS.map((p) => (
                  <option key={p} value={p}>{p === "不限" ? "平台不限" : p}</option>
                ))}
              </select>
            )}
            {!editing && (
              <select value={intent} onChange={(e) => setIntent(e.target.value)} className="rounded-md border border-white/10 bg-[#1c1f26] px-2 py-1 text-zinc-400 outline-none">
                {INTENTS.map((p) => (
                  <option key={p} value={p}>{p === "不限" ? "意图不限" : p}</option>
                ))}
              </select>
            )}
            {picked.length > 0 && (
              <div className="flex flex-wrap items-center gap-1">
                {picked.map((e) => (
                  <span key={e.id} className="flex items-center gap-1 rounded-full bg-sky-400/10 px-2 py-0.5 text-sky-300">
                    {e.name}
                    <button onClick={() => togglePicked(e)} className="text-sky-300/50 hover:text-sky-200">×</button>
                  </span>
                ))}
              </div>
            )}
          </div>
          <div className="flex items-end gap-2">
            <textarea
              ref={inputRef}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  void send();
                }
              }}
              rows={2}
              placeholder={editing ? "告诉 AI 想怎么改这一版…（Enter 发送 / Shift+Enter 换行）" : "描述你想创作的视频…（Enter 发送 / Shift+Enter 换行）"}
              className="min-w-0 flex-1 resize-none rounded-xl border border-white/10 bg-[#1c1f26] px-3 py-2 text-sm text-zinc-200 placeholder-zinc-600 outline-none focus:border-amber-300/30"
            />
            <button onClick={() => setPickerOpen(true)} className="shrink-0 rounded-xl border border-white/10 px-3 py-2.5 text-xs text-zinc-400 hover:border-sky-300/30 hover:text-sky-300">
              引用元素
            </button>
            <button
              onClick={() => void send()}
              disabled={sending}
              className="shrink-0 rounded-xl bg-amber-300/90 px-4 py-2.5 text-xs font-medium text-[#14161a] hover:bg-amber-300 disabled:opacity-50"
            >
              {sending ? "构思中…" : "发送"}
            </button>
          </div>
        </div>
      </div>

      {/* 元素选择弹窗 */}
      {pickerOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4" onClick={() => setPickerOpen(false)}>
          <div className="flex max-h-[80vh] w-full max-w-lg flex-col rounded-2xl border border-white/10 bg-[#191c21]" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between border-b border-white/5 px-4 py-3">
              <h3 className="text-sm font-semibold text-zinc-100">引用元素（{picked.length}）</h3>
              <button onClick={() => setPickerOpen(false)} className="text-zinc-500 hover:text-zinc-300">×</button>
            </div>
            <div className="px-4 py-2">
              <input
                value={pickerQ}
                onChange={(e) => setPickerQ(e.target.value)}
                placeholder="搜元素名 / 分类 / 描述…"
                className="w-full rounded-lg border border-white/10 bg-[#1f2228] px-3 py-1.5 text-sm text-zinc-200 placeholder-zinc-600 outline-none"
              />
            </div>
            <div className="flex-1 overflow-y-auto px-4 pb-3">
              {filterPicked.length === 0 && <div className="py-6 text-center text-xs text-zinc-600">没有可引用元素，先去拆解库产生元素</div>}
              {filterPicked.map((e) => {
                const on = picked.some((x) => x.id === e.id);
                return (
                  <button key={e.id} onClick={() => togglePicked(e)} className={`mb-1.5 flex w-full items-start gap-2 rounded-lg border px-3 py-2 text-left transition ${on ? "border-sky-300/40 bg-sky-400/10" : "border-white/5 bg-[#1c1f26] hover:bg-white/[0.04]"}`}>
                    <span className={`mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded border text-[10px] ${on ? "border-sky-300 bg-sky-400/30 text-sky-100" : "border-white/20 text-transparent"}`}>✓</span>
                    <span className="min-w-0">
                      <span className="flex flex-wrap items-center gap-1.5">
                        <span className="rounded bg-sky-400/10 px-1 py-0.5 text-[10px] text-sky-300">{e.category}</span>
                        <span className="text-xs font-medium text-zinc-200">{e.name}</span>
                        <span className="text-[10px] text-zinc-600">{STATUS_TAG[e.status] ?? e.status}</span>
                      </span>
                      {e.description && <span className="mt-0.5 line-clamp-2 block text-[11px] text-zinc-500">{e.description}</span>}
                    </span>
                  </button>
                );
              })}
            </div>
            <div className="border-t border-white/5 px-4 py-3 text-right">
              <button onClick={() => setPickerOpen(false)} className="rounded-lg bg-amber-300/90 px-4 py-1.5 text-xs font-medium text-[#14161a]">
                完成（{picked.length}）
              </button>
            </div>
          </div>
        </div>
      )}

      {/* 产物抽屉 */}
      {drawerOpen && (
        <div className="fixed inset-0 z-50 flex bg-black/60" onClick={() => setDrawerOpen(false)}>
          <div className="ml-auto flex h-full w-full max-w-md flex-col border-l border-white/10 bg-[#14161a]" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between border-b border-white/5 px-4 py-3">
              <h3 className="text-sm font-semibold text-zinc-100">产物库（{creations.length}）</h3>
              <button onClick={() => setDrawerOpen(false)} className="text-zinc-500 hover:text-zinc-300">×</button>
            </div>
            <div className="flex-1 overflow-y-auto">
              {creations.length === 0 && <div className="py-10 text-center text-xs text-zinc-600">还没有落卡产物，把满意的方案点「落卡保存」吧</div>}
              {creations.map((c) => (
                <button key={c.id} onClick={() => void openCreation(c)} className={`block w-full border-b border-white/5 px-4 py-3 text-left transition hover:bg-white/[0.03] ${activeCreation?.id === c.id ? "bg-white/[0.04]" : ""}`}>
                  <div className="flex items-center justify-between gap-2">
                    <span className="min-w-0 truncate text-sm font-medium text-zinc-200">{c.title}</span>
                    <span
                      onClick={(e) => {
                        e.stopPropagation();
                        void startEdit(c);
                      }}
                      className="shrink-0 cursor-pointer rounded border border-white/10 px-2 py-0.5 text-[10px] text-zinc-400 hover:border-amber-300/40 hover:text-amber-300"
                    >
                      继续修改
                    </span>
                  </div>
                  <div className="mt-1 flex items-center gap-2 text-[11px] text-zinc-500">
                    <span>{c.platform ?? "平台不限"}</span>
                    <span>{c.intent ?? "意图不限"}</span>
                    <span className="text-zinc-600">引用 {c.core_elements?.length ?? 0} 元素</span>
                  </div>
                </button>
              ))}
            </div>
            {activeCreation && (
              <div className="max-h-[55%] border-t border-white/10 bg-[#1c1f26]">
                <div className="flex items-center justify-between border-b border-white/5 px-4 py-2">
                  <div className="min-w-0">
                    <div className="truncate text-xs font-medium text-zinc-200">{activeCreation.title}</div>
                    <div className="text-[10px] text-zinc-500">落卡于 {fmtTime(activeCreation.created_at)}</div>
                  </div>
                  <div className="flex shrink-0 gap-1.5">
                    <button
                      onClick={() => {
                        const text = activeCreation.assets?.map((a) => a.content?.text ?? "").filter(Boolean).join("\n\n---\n\n");
                        if (text) copyText(text);
                      }}
                      className="rounded border border-white/10 px-2 py-1 text-[10px] text-zinc-300 hover:text-zinc-100"
                    >
                      复制
                    </button>
                    <button
                      onClick={() => {
                        if (confirmId === activeCreation.id) void removeCreation(activeCreation.id);
                        else {
                          setConfirmId(activeCreation.id);
                          setTimeout(() => setConfirmId((v) => (v === activeCreation.id ? null : v)), 3000);
                        }
                      }}
                      className={`rounded border px-2 py-1 text-[10px] ${confirmId === activeCreation.id ? "border-rose-400/40 bg-rose-400/15 text-rose-200" : "border-white/10 text-zinc-500 hover:text-rose-300"}`}
                    >
                      {confirmId === activeCreation.id ? "确认删除" : "删除"}
                    </button>
                  </div>
                </div>
                <div className="max-h-full overflow-y-auto px-4 py-3">
                  {activeCreation.assets?.map((a) => (
                    <div key={a.id} className="mb-3">
                      <div className="mb-1 flex items-center gap-2 text-[10px] uppercase tracking-wide text-zinc-600">
                        <span>{a.asset_type}</span>
                        {a.version ? <span className="rounded bg-sky-400/10 px-1 py-px text-[9px] normal-case text-sky-300">v{a.version}</span> : null}
                        <span className="text-[9px] normal-case text-zinc-700">{fmtTime(a.created_at)}</span>
                        {a.content?.text ? (
                          <button
                            onClick={() => void startEditFromAsset(a)}
                            className="ml-auto rounded border border-white/10 px-2 py-0.5 text-[9px] normal-case text-zinc-400 hover:border-amber-300/40 hover:text-amber-300"
                          >
                            以此版续改
                          </button>
                        ) : null}
                      </div>
                      <div className="whitespace-pre-wrap break-words rounded-lg bg-black/20 px-3 py-2 text-xs leading-relaxed text-zinc-300">{a.content?.text}</div>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
