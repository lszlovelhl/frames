import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { api, type CreationAssetView, type CreationDetail, type CreationItem, type CreationPublishItem, type ElementItem, type GuideChapter, type ProductInfo, type UsedElementRef, type UsedProductRef } from "../api";

interface Msg {
  role: "user" | "assistant";
  content: string;
  pending?: boolean;
  saved?: boolean;
  used?: UsedElementRef[];
  usedProducts?: UsedProductRef[];
}

interface GuideState {
  step: "previewing" | "confirm" | "generating" | "done" | "error";
  title?: string;
  chapters?: GuideChapter[];
  detail?: CreationDetail;
  error?: string;
}

const PLATFORMS = ["不限", "抖音", "哔哩哔哩", "小红书", "视频号", "YouTube"];
const INTENTS = ["不限", "涨粉", "带货", "品牌曝光", "教学/干货", "娱乐/剧情"];

const GUIDE_ORDER = [
  "guide_brief",
  "guide_strategy",
  "guide_script",
  "guide_shooting",
  "guide_editing",
  "guide_publish",
  "guide_checklist",
  "guide_sources",
] as const;

const GUIDE_TITLES: Record<string, string> = {
  guide_brief: "创作任务卡",
  guide_strategy: "核心策略",
  guide_script: "逐段脚本",
  guide_shooting: "拍摄执行单",
  guide_editing: "剪辑执行单",
  guide_publish: "发布运营",
  guide_checklist: "自查清单",
  guide_sources: "参考依据",
};

/** 极简 Markdown 渲染：标题/粗体/列表/表格/引用/分隔线，够用不引依赖 */
function inlineMd(text: string, keyBase: string): ReactNode[] {
  const out: ReactNode[] = [];
  const parts = text.split(/(\*\*[^*]+\*\*)/g);
  parts.forEach((p, i) => {
    if (!p) return;
    if (p.startsWith("**") && p.endsWith("**")) {
      out.push(<strong key={`${keyBase}-b${i}`} className="font-semibold text-zinc-100">{p.slice(2, -2)}</strong>);
    } else {
      out.push(<span key={`${keyBase}-s${i}`}>{p}</span>);
    }
  });
  return out;
}

function parseTableRow(line: string): string[] {
  return line
    .split("|")
    .slice(1, -1)
    .map((c) => c.trim());
}

function renderMdLines(text: string): ReactNode[] {
  const lines = text.split("\n");
  const nodes: ReactNode[] = [];
  let i = 0;
  let key = 0;
  while (i < lines.length) {
    const line = lines[i];
    const trimmed = line.trim();
    if (!trimmed) {
      i += 1;
      continue;
    }
    if (trimmed === "---" || trimmed === "***") {
      nodes.push(<hr key={key++} className="my-2 border-white/10" />);
      i += 1;
      continue;
    }
    const heading = /^(#{1,6})\s+(.*)$/.exec(trimmed);
    if (heading) {
      const level = heading[1].length;
      const cls = level <= 2 ? "text-base font-semibold text-zinc-100 my-2" : level === 3 ? "text-sm font-semibold text-zinc-200 mt-2.5 mb-1" : "text-xs font-medium text-zinc-300 mt-2 mb-0.5";
      const Tag = level <= 2 ? "h3" : level === 3 ? "h4" : "h5";
      nodes.push(<Tag key={key++} className={cls}>{inlineMd(heading[2], `h${key}`)}</Tag>);
      i += 1;
      continue;
    }
    // 引用
    if (trimmed.startsWith(">")) {
      nodes.push(<blockquote key={key++} className="my-1.5 border-l-2 border-amber-300/30 pl-2 text-xs text-zinc-400">{inlineMd(trimmed.replace(/^>\s?/, ""), `q${key}`)}</blockquote>);
      i += 1;
      continue;
    }
    // 表格：连续两行（表头 + 分隔行）+ 后续行
    if (i + 1 < lines.length && trimmed.includes("|") && /^\s*\|?[\s:|-]+\|?\s*$/.test(lines[i + 1].trim()) && /^\|/.test(trimmed)) {
      const header = parseTableRow(trimmed);
      const bodyStart = i + 2;
      const body: string[][] = [];
      let j = bodyStart;
      while (j < lines.length && lines[j].includes("|") && /^\|/.test(lines[j].trim())) {
        body.push(parseTableRow(lines[j]));
        j += 1;
      }
      nodes.push(
        <div key={key++} className="my-2 overflow-x-auto">
          <table className="w-full border-collapse text-xs">
            <thead>
              <tr>{header.map((h, c) => <th key={c} className="border border-white/10 bg-white/5 px-2 py-1 text-left font-medium text-zinc-300">{inlineMd(h, `th${key}${c}`)}</th>)}</tr>
            </thead>
            <tbody>
              {body.map((row, r) => (
                <tr key={r}>{row.map((cell, c) => <td key={c} className="border border-white/10 px-2 py-1 align-top text-zinc-400">{inlineMd(cell, `td${key}${r}${c}`)}</td>)}</tr>
              ))}
            </tbody>
          </table>
        </div>
      );
      i = j;
      continue;
    }
    // 有序列表
    if (/^\d+[.、)]\s/.test(trimmed)) {
      const items: string[] = [];
      while (i < lines.length && /^\d+[.、)]\s/.test(lines[i].trim())) {
        items.push(lines[i].trim().replace(/^\d+[.、)]\s/, ""));
        i += 1;
      }
      nodes.push(
        <ol key={key++} className="my-1 list-decimal space-y-0.5 pl-4">
          {items.map((it, c) => <li key={c} className="text-xs leading-relaxed text-zinc-400">{inlineMd(it, `ol${key}${c}`)}</li>)}
        </ol>
      );
      continue;
    }
    // 无序列表（含嵌套 -/+ 开头）
    if (/^\s*[-*+]\s+/.test(trimmed)) {
      const items: { raw: string; depth: number }[] = [];
      while (i < lines.length && /^\s*[-*+]\s+/.test(lines[i])) {
        const m = /^(\s*)[-*+]\s+(.*)$/.exec(lines[i])!;
        const depth = Math.floor(m[1].length / 2);
        items.push({ raw: m[2], depth });
        i += 1;
      }
      nodes.push(
        <ul key={key++} className="my-1 space-y-0.5">
          {items.map((it, c) => (
            <li key={c} className="text-xs leading-relaxed text-zinc-400" style={{ paddingLeft: `${it.depth * 0.8}rem` }}>
              <span className="mr-1 text-zinc-600">{it.depth === 0 ? "•" : "◦"}</span>
              {inlineMd(it.raw, `ul${key}${c}`)}
            </li>
          ))}
        </ul>
      );
      continue;
    }
    // 普通段落
    const para: string[] = [trimmed];
    i += 1;
    while (i < lines.length && lines[i].trim() && !/^(#{1,6})\s|^\s*[-*+]\s+|\d+[.、)]\s|^\|/.test(lines[i].trim()) && lines[i].trim() !== "---") {
      para.push(lines[i].trim());
      i += 1;
    }
    nodes.push(<p key={key++} className="my-1 text-xs leading-relaxed text-zinc-400">{inlineMd(para.join(" "), `p${key}`)}</p>);
  }
  return nodes;
}

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

interface CreateViewProps {
  seedElementIds?: string[];
  seedProductIds?: string[];
}

export default function CreateView({ seedElementIds, seedProductIds }: CreateViewProps) {
  const [messages, setMessages] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [platform, setPlatform] = useState("不限");
  const [intent, setIntent] = useState("不限");
  const [picked, setPicked] = useState<ElementItem[]>([]);
  const [pickedProducts, setPickedProducts] = useState<ProductInfo[]>([]);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [productPickerOpen, setProductPickerOpen] = useState(false);
  const [pickerQ, setPickerQ] = useState("");
  const [productQ, setProductQ] = useState("");
  const [allElements, setAllElements] = useState<ElementItem[]>([]);
  const [allProducts, setAllProducts] = useState<ProductInfo[]>([]);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [creations, setCreations] = useState<CreationItem[]>([]);
  const [activeCreation, setActiveCreation] = useState<CreationDetail | null>(null);
  const [confirmId, setConfirmId] = useState<string | null>(null);
  const [editing, setEditing] = useState<{ creationId: string; title: string; version: number; baseAssetId: string | null } | null>(null);
  const [guide, setGuide] = useState<GuideState | null>(null);
  const [guideTipOpen, setGuideTipOpen] = useState<string | null>(null); // trace 浮层 key: `${assetType}:${idx}`
  const [guideTab, setGuideTab] = useState<string>("guide_brief");
  const [guideCreationIds, setGuideCreationIds] = useState<Set<string>>(new Set());
  const endRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLTextAreaElement | null>(null);

  useEffect(() => {
    api
      .listElements({})
      .then((d) => setAllElements(d.items ?? []))
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
    api
      .listProducts()
      .then((d) => setAllProducts(d.items ?? []))
      .catch(() => {
        /* 产品库未就绪不阻塞创作 */
      });
  }, []);

  // 从元素库带入选中的元素：元素加载完成后自动放入待引用区
  useEffect(() => {
    if (!seedElementIds?.length || allElements.length === 0) return;
    const seed = allElements.filter((e) => seedElementIds.includes(e.id));
    if (seed.length === 0) return;
    setPicked((prev) => {
      const have = new Set(prev.map((x) => x.id));
      return [...prev, ...seed.filter((x) => !have.has(x.id))];
    });
    setNotice(`已从元素库带入 ${seed.length} 个元素，直接输入需求即可生成`);
  }, [seedElementIds, allElements]);

  // 从产品库带入选中产品：产品加载完成后自动放入待引用区
  useEffect(() => {
    if (!seedProductIds?.length || allProducts.length === 0) return;
    const seed = allProducts.filter((p) => seedProductIds.includes(p.id));
    if (seed.length === 0) return;
    setPickedProducts((prev) => {
      const have = new Set(prev.map((x) => x.id));
      return [...prev, ...seed.filter((x) => !have.has(x.id))];
    });
    setNotice(`已从产品库带入 ${seed.length} 个产品，AI 会自然融入种草点`);
  }, [seedProductIds, allProducts]);

  useEffect(() => {
    if (drawerOpen) {
      api
        .listCreations()
        .then((d) => {
          const items = d.creations ?? [];
          setCreations(items);
          void Promise.all(
            items.map(async (c) => {
              try {
                const det = await api.getCreation(c.id);
                return det.assets?.some((a) => a.asset_type === "guide_brief") ? c.id : null;
              } catch {
                return null;
              }
            })
          ).then((arr) => setGuideCreationIds(new Set(arr.filter((x): x is string => Boolean(x)))));
        })
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

  const filterProducts = useMemo(() => {
    const q = productQ.trim().toLowerCase();
    if (!q) return allProducts;
    return allProducts.filter((p) =>
      (p.brand + p.name + (p.series ?? "") + p.headline + p.industry + p.category_tags.join(" ")).toLowerCase().includes(q)
    );
  }, [allProducts, productQ]);

  function togglePicked(e: ElementItem) {
    setPicked((prev) => (prev.some((x) => x.id === e.id) ? prev.filter((x) => x.id !== e.id) : [...prev, e]));
  }

  function togglePickedProduct(p: ProductInfo) {
    setPickedProducts((prev) => (prev.some((x) => x.id === p.id) ? prev.filter((x) => x.id !== p.id) : [...prev, p]));
  }

  function productLabel(p: ProductInfo): string {
    return p.name ? `${p.brand} ${p.name}` : `${p.brand} ${p.series}`;
  }

  async function send() {
    const text = input.trim();
    if ((!text && picked.length === 0 && pickedProducts.length === 0) || sending) return;
    const userMsg: Msg = {
      role: "user",
      content: text || `请结合我选的 ${picked.length} 个元素、${pickedProducts.length} 个产品生成一版可落地的创作方案`,
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
        ? await api.creationsContinueChat(editing.creationId, history, picked.map((e) => e.id), pickedProducts.map((p) => p.id))
        : await api.creationsChat(
            history,
            picked.map((e) => e.id),
            pickedProducts.map((p) => p.id),
            platform !== "不限" ? platform : undefined,
            intent !== "不限" ? intent : undefined
          );
      setMessages((prev) => {
        const copy = prev.slice();
        copy[copy.length - 1] = { role: "assistant", content: r.reply, used: r.used_elements ?? [], usedProducts: r.used_products ?? [] };
        return copy;
      });
      setPicked([]);
      setPickedProducts([]);
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
        const d1 = await api.listCreations();
        setCreations(d1.creations ?? []);
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
      // 无条件刷新产物列表，保证顶栏计数与抽屉即时更新
      const d2 = await api.listCreations();
      setCreations(d2.creations ?? []);
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
      setGuideTab("guide_brief");
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

  function guideUserPrompt(): string {
    const lastUser = [...messages].reverse().find((m) => m.role === "user")?.content.trim();
    const need = input.trim();
    return need || lastUser || "";
  }

  function hasGuideInput(): boolean {
    return guideUserPrompt().length > 0 || picked.length > 0 || pickedProducts.length > 0;
  }

  async function startGuidePreview() {
    if (!hasGuideInput()) {
      setError("先在对话区描述创作需求（可引用元素/产品），再生成完整指南");
      return;
    }
    if (guide && guide.step !== "error") {
      setError("创作指南正在生成中，请稍候");
      return;
    }
    setGuide({ step: "previewing" });
    setSending(true);
    setError("");
    try {
      const prompt = guideUserPrompt();
      const r = await api.guidePreview({
        messages: [{ role: "user", content: prompt }],
        element_ids: picked.map((e) => e.id),
        product_ids: pickedProducts.map((p) => p.id),
        platform: platform !== "不限" ? platform : undefined,
        intent: intent !== "不限" ? intent : undefined,
      });
      setGuide({ step: "confirm", title: r.title, chapters: r.chapters });
    } catch (e) {
      setGuide({ step: "error", error: e instanceof Error ? e.message : String(e) });
    } finally {
      setSending(false);
    }
  }

  async function confirmGuideGenerate() {
    if (!guide || guide.step !== "confirm") return;
    setGuide((prev) => (prev ? { ...prev, step: "generating" } : prev));
    setSending(true);
    setError("");
    try {
      const brief = guide.chapters?.find((c) => c.asset === "brief");
      const strategy = guide.chapters?.find((c) => c.asset === "strategy");
      if (!brief || !strategy) throw new Error("速览数据缺失");
      const r = await api.guideGenerate({
        messages: [{ role: "user", content: guideUserPrompt() }],
        title: guide.title ?? "",
        preview: [brief, strategy],
        element_ids: picked.map((e) => e.id),
        product_ids: pickedProducts.map((p) => p.id),
        platform: platform !== "不限" ? platform : undefined,
        intent: intent !== "不限" ? intent : undefined,
      });
      if (!r.id) throw new Error("指南已生成但缺少记录 id");
      const d = await api.getCreation(r.id);
      setGuide((prev) => (prev ? { ...prev, step: "done", title: r.title, detail: d } : prev));
      setPicked([]);
      setPickedProducts([]);
    } catch (e) {
      setGuide((prev) => (prev ? { ...prev, step: "error", error: e instanceof Error ? e.message : String(e) } : prev));
    } finally {
      setSending(false);
    }
  }

  function guideChaptersOf(cd: CreationDetail): CreationAssetView[] {
    const assets = cd.assets ?? [];
    return GUIDE_ORDER.map((t) => assets.find((a) => a.asset_type === t)).filter((a): a is CreationAssetView => Boolean(a));
  }

  function isGuideCreation(cd: CreationDetail | null): boolean {
    return Boolean(cd && (cd.assets ?? []).some((a) => a.asset_type === "guide_brief"));
  }

  function openGuideDetail(c: CreationItem) {
    void openCreation(c).then(() => {
      setDrawerOpen(true);
    });
  }

  function closeGuide() {
    setGuide(null);
    setGuideTipOpen(null);
  }

  function renderTraceChips(chapters: GuideChapter[] | undefined, asset: string) {
    const ch = chapters?.find((c) => c.asset === asset);
    const traces = ch?.trace ?? [];
    if (!traces.length) return null;
    const openKey = `${asset}:chip`;
    return (
      <div className="mt-1.5">
        <div className="flex flex-wrap gap-1">
          {traces.map((t, idx) => (
            <button
              key={`${asset}-${idx}`}
              onClick={() => setGuideTipOpen((v) => (v === `${openKey}-${idx}` ? null : `${openKey}-${idx}`))}
              className="inline-flex items-center gap-0.5 rounded bg-sky-400/10 px-1.5 py-0.5 text-[10px] text-sky-300/90 hover:bg-sky-400/20"
            >
              <span className="font-semibold">ⓘ</span>
              <span className="truncate">{t.label || t.name}</span>
            </button>
          ))}
        </div>
        {traces.map((t, idx) => {
          if (guideTipOpen !== `${openKey}-${idx}`) return null;
          return (
            <div key={idx} className="mt-1 rounded-lg border border-sky-300/20 bg-[#16212b] px-2.5 py-1.5 text-[10px] leading-relaxed text-sky-200/80">
              来源：{t.kind === "element" ? "元素库" : "种草产品"}
              <span className="ml-1 text-sky-300/50">（{t.label || t.name}）</span>
              {t.time ? <span className="ml-1 text-sky-300/60">· 时间点 {t.time}</span> : null}
            </div>
          );
        })}
      </div>
    );
  }

  function renderGuidePanel() {
    if (!guide) return null;
    const chs = guide.chapters ?? [];
    return (
      <div className="overflow-hidden rounded-2xl border border-violet-300/20 bg-[#1b1724]">
        {guide.step === "previewing" && (
          <div className="flex items-center gap-2 px-4 py-4 text-sm text-zinc-300">
            <span className="inline-block h-3.5 w-3.5 animate-spin rounded-full border border-zinc-600 border-t-violet-300" />
            AI 正在生成创作任务卡与核心策略（速览）…
          </div>
        )}
        {guide.step === "generating" && (
          <div className="flex items-center gap-2 px-4 py-4 text-sm text-zinc-300">
            <span className="inline-block h-3.5 w-3.5 animate-spin rounded-full border border-zinc-600 border-t-violet-300" />
            方向已确认，正在补全脚本 / 拍摄 / 剪辑 / 发布清单，全程约需 1 分钟…
          </div>
        )}
        {guide.step === "confirm" && (
          <div className="max-h-[70vh] overflow-y-auto">
            <div className="border-b border-white/5 px-4 py-3">
              <div className="flex items-center gap-2">
                <span className="rounded bg-violet-400/15 px-1.5 py-0.5 text-[10px] font-medium text-violet-300">创作指南 · 速览</span>
                {guide.title && <span className="truncate text-sm font-medium text-zinc-100">{guide.title}</span>}
              </div>
              <p className="mt-1 text-[11px] text-zinc-500">先确认方向（任务卡 + 核心策略），没问题再一键补全完整执行指南</p>
            </div>
            {chs.map((c) => (
              <div key={c.asset} className="border-b border-white/5 px-4 py-3">
                <div className="mb-1.5 flex items-center gap-1.5 text-xs font-medium text-violet-200/90">
                  <span className="text-[10px] text-zinc-600">{c.asset.toUpperCase()}</span>
                  {c.title}
                </div>
                <div className="text-sm leading-relaxed text-zinc-300">{renderMdLines(c.text ?? "")}</div>
                {renderTraceChips(chs, c.asset)}
              </div>
            ))}
            <div className="flex flex-wrap items-center gap-2 px-4 py-3">
              <button onClick={() => void confirmGuideGenerate()} disabled={sending} className="rounded-lg bg-violet-300/90 px-4 py-2 text-xs font-medium text-[#14161a] hover:bg-violet-300 disabled:opacity-50">
                {sending ? "生成中…" : "没问题，继续生成完整指南 →"}
              </button>
              <button
                onClick={() => {
                  closeGuide();
                  setNotice("已取消，可重新发起或继续对话");
                }}
                className="rounded-lg border border-white/10 px-3 py-2 text-xs text-zinc-400 hover:text-zinc-200"
              >
                不满意，重新描述
              </button>
            </div>
          </div>
        )}
        {guide.step === "done" && guide.detail && (
          <div>
            <div className="flex items-center justify-between border-b border-white/5 px-4 py-3">
              <div className="flex items-center gap-2">
                <span className="rounded bg-emerald-400/15 px-1.5 py-0.5 text-[10px] font-medium text-emerald-300">已生成完整指南</span>
                <span className="truncate text-sm font-medium text-zinc-100">{guide.title ?? guide.detail.title}</span>
              </div>
              <button onClick={closeGuide} className="rounded border border-white/10 px-2 py-1 text-[10px] text-zinc-400 hover:text-zinc-200">×</button>
            </div>
            <div className="px-4 py-3">
              <p className="mb-2 text-[11px] text-zinc-500">已按 8 个章节沉淀到产物库，含逐段脚本 / 拍摄 / 剪辑 / 发布执行单与参考依据。</p>
              <div className="mb-3 flex flex-wrap gap-1">
                {guideChaptersOf(guide.detail).map((a, idx) => (
                  <span key={a.id} className="rounded bg-violet-400/10 px-1.5 py-0.5 text-[10px] text-violet-300/90">
                    {idx + 1}. {GUIDE_TITLES[a.asset_type] ?? a.asset_type}
                  </span>
                ))}
              </div>
              <div className="flex flex-wrap gap-2">
                <button
                  onClick={() => {
                    const item = guide.detail;
                    if (item) openGuideDetail({ id: item.id, title: item.title, platform: item.platform, intent: item.intent, core_elements: item.core_elements, created_at: item.created_at, updated_at: item.updated_at } as CreationItem);
                  }}
                  className="rounded-lg bg-violet-300/90 px-4 py-2 text-xs font-medium text-[#14161a] hover:bg-violet-300"
                >
                  在产物库查看指南
                </button>
                <button onClick={closeGuide} className="rounded-lg border border-white/10 px-3 py-2 text-xs text-zinc-400 hover:text-zinc-200">
                  继续创作
                </button>
              </div>
            </div>
          </div>
        )}
        {guide.step === "error" && (
          <div className="flex items-start justify-between gap-2 px-4 py-3">
            <p className="text-xs leading-relaxed text-rose-300">指南生成失败：{guide.error ?? "未知错误"}</p>
            <div className="flex shrink-0 gap-2">
              <button onClick={() => void startGuidePreview()} className="rounded border border-white/10 px-2 py-1 text-[10px] text-zinc-300 hover:text-zinc-100">重试</button>
              <button onClick={closeGuide} className="rounded border border-white/10 px-2 py-1 text-[10px] text-zinc-500 hover:text-zinc-300">关闭</button>
            </div>
          </div>
        )}
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col">
      {/* 顶栏 */}
      <header className="flex items-center justify-between border-b border-white/5 px-6 py-3">
        <div>
          <h1 className="text-lg font-semibold text-zinc-100">创作台</h1>
          <p className="text-xs text-zinc-500">{editing ? "改稿模式 · 针对当前版本迭代" : "对话驱动 · 可引用元素库 · 一键落卡沉淀 · 可生成完整创作指南"}</p>
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
                  也可以先点下方「引用元素」把拆解库里提炼的爆款元素带上，或点「选产品」带入想种草的产品，AI 会基于它们生成方案。
                  <br />
                  想要一份「照着做就能出片」的完整指南（任务卡 / 策略 / 逐段脚本 / 拍摄剪辑发布清单），点右下角「✦ 创作指南」即可两步生成。
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
                    {m.usedProducts && m.usedProducts.length > 0 && (
                      <div className="mt-2 flex flex-wrap gap-1">
                        {m.usedProducts.map((u) => (
                          <span key={u.id} className="rounded bg-amber-300/10 px-1.5 py-0.5 text-[10px] text-amber-200/90">
                            ◈ {u.brand} {u.name}
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
          {renderGuidePanel()}
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
            {pickedProducts.length > 0 && (
              <div className="flex flex-wrap items-center gap-1">
                {pickedProducts.map((p) => (
                  <span key={p.id} className="flex items-center gap-1 rounded-full bg-amber-300/10 px-2 py-0.5 text-amber-200">
                    ◈ {productLabel(p)}
                    <button onClick={() => togglePickedProduct(p)} className="text-amber-200/50 hover:text-amber-100">×</button>
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
            <button onClick={() => setProductPickerOpen(true)} className="shrink-0 rounded-xl border border-white/10 px-3 py-2.5 text-xs text-zinc-400 hover:border-amber-300/30 hover:text-amber-300">
              选产品
            </button>
            {!editing && (
              <button
                onClick={() => void startGuidePreview()}
                disabled={sending}
                className="shrink-0 rounded-xl border border-violet-300/30 bg-violet-400/10 px-3 py-2.5 text-xs text-violet-200 hover:bg-violet-400/20 disabled:opacity-50"
                title="先生成任务卡+策略速览，确认后补全拍摄/剪辑/发布执行清单"
              >
                ✦ 创作指南
              </button>
            )}
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

      {/* 产品选择弹窗 */}
      {productPickerOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4" onClick={() => setProductPickerOpen(false)}>
          <div className="flex max-h-[80vh] w-full max-w-xl flex-col rounded-2xl border border-white/10 bg-[#191c21]" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between border-b border-white/5 px-4 py-3">
              <h3 className="text-sm font-semibold text-zinc-100">选产品（{pickedProducts.length}）</h3>
              <button onClick={() => setProductPickerOpen(false)} className="text-zinc-500 hover:text-zinc-300">×</button>
            </div>
            <div className="px-4 py-2">
              <input
                value={productQ}
                onChange={(e) => setProductQ(e.target.value)}
                placeholder="搜品牌 / 产品 / 行业 / 卖点…"
                className="w-full rounded-lg border border-white/10 bg-[#1f2228] px-3 py-1.5 text-sm text-zinc-200 placeholder-zinc-600 outline-none"
              />
            </div>
            <div className="flex-1 overflow-y-auto px-4 pb-3">
              {filterProducts.length === 0 && <div className="py-6 text-center text-xs text-zinc-600">暂无产品，可先去产品库维护</div>}
              {filterProducts.map((p) => {
                const on = pickedProducts.some((x) => x.id === p.id);
                return (
                  <button key={p.id} onClick={() => togglePickedProduct(p)} className={`mb-1.5 flex w-full items-start gap-2 rounded-lg border px-3 py-2 text-left transition ${on ? "border-amber-300/40 bg-amber-300/10" : "border-white/5 bg-[#1c1f26] hover:bg-white/[0.04]"}`}>
                    <span className={`mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded border text-[10px] ${on ? "border-amber-300 bg-amber-300/30 text-amber-100" : "border-white/20 text-transparent"}`}>✓</span>
                    <span className="min-w-0 flex-1">
                      <span className="flex flex-wrap items-center gap-1.5">
                        <span className="rounded bg-amber-300/10 px-1 py-0.5 text-[10px] text-amber-200">{p.industry}</span>
                        <span className="text-xs font-semibold text-zinc-100">{p.brand}</span>
                        <span className="text-xs font-medium text-zinc-200">{p.name}</span>
                        {p.price_range && <span className="text-[10px] text-zinc-500">{p.price_range}</span>}
                      </span>
                      <span className="mt-0.5 line-clamp-1 block text-[11px] text-zinc-500">{p.headline}</span>
                      <span className="mt-0.5 flex flex-wrap gap-1">
                        {p.selling_points.slice(0, 2).map((s) => (
                          <span key={s.title} className="rounded bg-white/5 px-1 py-0.5 text-[10px] text-zinc-400">{s.title}</span>
                        ))}
                      </span>
                    </span>
                  </button>
                );
              })}
            </div>
            <div className="border-t border-white/5 px-4 py-3 text-right">
              <button onClick={() => setProductPickerOpen(false)} className="rounded-lg bg-amber-300/90 px-4 py-1.5 text-xs font-medium text-[#14161a]">
                完成（{pickedProducts.length}）
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
                    <span className="flex min-w-0 items-center gap-1.5">
                      {guideCreationIds.has(c.id) && (
                        <span className="shrink-0 rounded bg-violet-400/15 px-1 py-px text-[9px] text-violet-300">指南</span>
                      )}
                      <span className="truncate text-sm font-medium text-zinc-200">{c.title}</span>
                    </span>
                    {!guideCreationIds.has(c.id) && (
                      <span
                        onClick={(e) => {
                          e.stopPropagation();
                          void startEdit(c);
                        }}
                        className="shrink-0 cursor-pointer rounded border border-white/10 px-2 py-0.5 text-[10px] text-zinc-400 hover:border-amber-300/40 hover:text-amber-300"
                      >
                        继续修改
                      </span>
                    )}
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
                  {isGuideCreation(activeCreation) ? (
                    (() => {
                      const guideAssets = guideChaptersOf(activeCreation);
                      const active = guideAssets.find((a) => a.asset_type === guideTab) ?? guideAssets[0];
                      const traceChips = active?.content?.trace ?? [];
                      return (
                        <div>
                          <div className="mb-3 flex flex-wrap gap-1">
                            {guideAssets.map((a, idx) => {
                              const on = a.asset_type === active?.asset_type;
                              return (
                                <button
                                  key={a.id}
                                  onClick={() => setGuideTab(a.asset_type)}
                                  className={`rounded-full px-2 py-1 text-[11px] transition ${on ? "bg-violet-400/20 text-violet-200" : "bg-white/5 text-zinc-500 hover:text-zinc-300"}`}
                                >
                                  {idx + 1}. {GUIDE_TITLES[a.asset_type] ?? a.asset_type}
                                </button>
                              );
                            })}
                          </div>
                          {active?.asset_type === "guide_sources" ? (
                            <div>
                              <p className="mb-2 text-[11px] text-zinc-500">聚合自各章节正文 ⓘ 标注的来源素材（已去重）。</p>
                              {active.content?.items?.length ? (
                                active.content.items.map((it, idx) => (
                                  <div key={idx} className="mb-2 rounded-lg border border-white/5 bg-black/20 px-3 py-2">
                                    <div className="flex items-center gap-1.5 text-xs text-zinc-200">
                                      <span className={`rounded px-1 py-px text-[9px] ${it.kind === "element" ? "bg-sky-400/15 text-sky-300" : "bg-amber-300/15 text-amber-200"}`}>
                                        {it.kind === "element" ? "元素" : "产品"}
                                      </span>
                                      <span className="font-medium">{it.label || it.name}</span>
                                    </div>
                                    <div className="mt-1 flex flex-wrap gap-1">
                                      {it.chapters?.map((ch) => (
                                        <span key={ch} className="rounded bg-white/5 px-1 py-px text-[9px] text-zinc-500">{GUIDE_TITLES[`guide_${ch}`] ?? ch}</span>
                                      ))}
                                    </div>
                                  </div>
                                ))
                              ) : (
                                <div className="py-6 text-center text-xs text-zinc-600">本指南未标注来源素材</div>
                              )}
                            </div>
                          ) : (
                            <div>
                              {active?.content?.text ? (
                                <div className="text-xs leading-relaxed text-zinc-300">{renderMdLines(active.content.text)}</div>
                              ) : (
                                <div className="py-6 text-center text-xs text-zinc-600">该章节暂无内容</div>
                              )}
                              {traceChips.length > 0 && (
                                <div className="mt-2 border-t border-white/5 pt-2">
                                  <div className="mb-1 text-[10px] text-zinc-600">本章参考来源</div>
                                  <div className="flex flex-wrap gap-1">
                                    {traceChips.map((t, idx) => (
                                      <button
                                        key={`${active.asset_type}-${idx}`}
                                        onClick={() => setGuideTipOpen((v) => (v === `drawer:${active.asset_type}:${idx}` ? null : `drawer:${active.asset_type}:${idx}`))}
                                        className="inline-flex items-center gap-0.5 rounded bg-sky-400/10 px-1.5 py-0.5 text-[10px] text-sky-300/90 hover:bg-sky-400/20"
                                      >
                                        <span className="font-semibold">ⓘ</span>
                                        <span className="truncate">{t.label || t.name}</span>
                                      </button>
                                    ))}
                                  </div>
                                  {traceChips.map((t, idx) => {
                                    if (guideTipOpen !== `drawer:${active.asset_type}:${idx}`) return null;
                                    return (
                                      <div key={idx} className="mt-1 rounded-lg border border-sky-300/20 bg-[#16212b] px-2.5 py-1.5 text-[10px] leading-relaxed text-sky-200/80">
                                        来源：{t.kind === "element" ? "元素库" : "种草产品"}
                                        <span className="ml-1 text-sky-300/50">（{t.label || t.name}）</span>
                                        {t.time ? <span className="ml-1 text-sky-300/60">· 时间点 {t.time}</span> : null}
                                      </div>
                                    );
                                  })}
                                </div>
                              )}
                            </div>
                          )}
                        </div>
                      );
                    })()
                  ) : (
                    activeCreation.assets?.map((a) => {
                      const parentVer = a.parent_id ? activeCreation.assets?.find((p) => p.id === a.parent_id)?.version : null;
                      return (
                        <div key={a.id} className="mb-3">
                          <div className="mb-1 flex items-center gap-2 text-[10px] uppercase tracking-wide text-zinc-600">
                            <span>{a.asset_type}</span>
                            {a.version ? <span className="rounded bg-sky-400/10 px-1 py-px text-[9px] normal-case text-sky-300">v{a.version}</span> : null}
                            {a.parent_id && parentVer != null ? (
                              <span className="rounded bg-zinc-400/10 px-1 py-px text-[9px] normal-case text-zinc-500">衍生自 v{parentVer}</span>
                            ) : null}
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
                      );
                    })
                  )}

                  <CreationPublishBox creationId={activeCreation.id} />
                </div>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

const PUBLISH_PLATFORM_OPTIONS = ["抖音", "哔哩哔哩", "小红书", "视频号", "YouTube"];

/** A2 发布回流：创作台产物抽屉里的登记表单 + 已登记列表（样式与产物卡一致） */
function CreationPublishBox({ creationId }: { creationId: string }) {
  const [items, setItems] = useState<CreationPublishItem[] | null>(null);
  const [error, setError] = useState("");
  const [formOpen, setFormOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [platform, setPlatform] = useState(PUBLISH_PLATFORM_OPTIONS[0]);
  const [url, setUrl] = useState("");
  const [pubAt, setPubAt] = useState("");
  const [statsText, setStatsText] = useState("");
  const [confirmId, setConfirmId] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const d = await api.listCreationPublishes(creationId);
      setItems(d.items);
      setError("");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [creationId]);

  useEffect(() => {
    setItems(null);
    setFormOpen(false);
    void load();
  }, [load]);

  async function submit() {
    const name = platform.trim();
    if (!name) {
      setError("请填写发布平台");
      return;
    }
    let stats: Record<string, unknown> | null = null;
    const t = statsText.trim();
    if (t) {
      try {
        const v: unknown = JSON.parse(t);
        if (typeof v !== "object" || v === null || Array.isArray(v)) throw new Error("stats 需为 JSON 对象");
        stats = v as Record<string, unknown>;
      } catch (e) {
        setError(`stats 不是合法 JSON：${e instanceof Error ? e.message : String(e)}`);
        return;
      }
    }
    setBusy(true);
    setError("");
    try {
      const publishedAt = pubAt ? new Date(pubAt).toISOString() : undefined;
      await api.registerCreationPublish(creationId, {
        publish_platform: name,
        publish_url: url.trim() || null,
        published_at: publishedAt,
        stats,
      });
      setUrl("");
      setPubAt("");
      setStatsText("");
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  function remove(id: string) {
    if (confirmId === id) {
      setConfirmId(null);
      void (async () => {
        try {
          await api.removeCreationPublish(creationId, id);
          await load();
        } catch (e) {
          setError(e instanceof Error ? e.message : String(e));
        }
      })();
    } else {
      setConfirmId(id);
      setTimeout(() => setConfirmId((v) => (v === id ? null : v)), 3000);
    }
  }

  return (
    <div className="mb-3 rounded-lg border border-white/5 bg-[#1c1f26] p-3">
      <div className="mb-1 flex items-center gap-2">
        <span className="text-[10px] uppercase tracking-wider text-zinc-500">发布回流</span>
        {items && items.length > 0 && (
          <span className="rounded bg-amber-300/10 px-1.5 py-px text-[9px] text-amber-200/90">{items.length} 条</span>
        )}
        <button
          onClick={() => setFormOpen((v) => !v)}
          className="ml-auto rounded border border-white/10 px-2 py-0.5 text-[9px] normal-case text-zinc-400 hover:border-amber-300/40 hover:text-amber-300"
        >
          {formOpen ? "收起登记" : "登记发布"}
        </button>
      </div>

      {error && <div className="mb-2 rounded bg-rose-400/10 px-2 py-1 text-[10px] text-rose-300">{error}</div>}

      {formOpen && (
        <div className="mb-2 space-y-2 rounded-lg border border-white/5 bg-black/20 p-2.5">
          <div className="flex flex-wrap gap-2">
            <select
              value={platform}
              onChange={(e) => setPlatform(e.target.value)}
              className="rounded border border-white/10 bg-[#14161a] px-2 py-1.5 text-[11px] text-zinc-200 outline-none"
            >
              {PUBLISH_PLATFORM_OPTIONS.map((p) => (
                <option key={p} value={p}>{p}</option>
              ))}
            </select>
            <input
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              placeholder="发布链接（可留空）"
              className="min-w-0 flex-1 rounded border border-white/10 bg-[#14161a] px-2 py-1.5 text-[11px] text-zinc-200 placeholder-zinc-600 outline-none"
            />
          </div>
          <input
            type="datetime-local"
            value={pubAt}
            onChange={(e) => setPubAt(e.target.value)}
            className="rounded border border-white/10 bg-[#14161a] px-2 py-1.5 text-[11px] text-zinc-200 outline-none"
          />
          <textarea
            value={statsText}
            onChange={(e) => setStatsText(e.target.value)}
            spellCheck={false}
            rows={2}
            placeholder='回流数据（可留空，JSON 对象）：{"view_count": 128000, "like_count": 9200}'
            className="w-full resize-y rounded border border-white/10 bg-[#14161a] px-2 py-1.5 font-mono text-[10px] text-zinc-300 placeholder-zinc-600 outline-none"
          />
          <button
            disabled={busy}
            onClick={() => void submit()}
            className="rounded bg-amber-300/90 px-3 py-1 text-[10px] font-medium text-[#14161a] disabled:opacity-40"
          >
            {busy ? "登记中…" : "登记"}
          </button>
        </div>
      )}

      {items === null ? (
        <div className="py-2 text-[10px] text-zinc-600">加载中…</div>
      ) : items.length === 0 ? (
        <div className="py-2 text-[10px] text-zinc-600">暂无已登记的发布回流</div>
      ) : (
        <div className="space-y-1.5">
          {items.map((p) => (
            <div key={p.id} className="flex flex-wrap items-center gap-1.5 rounded-lg border border-white/5 bg-black/20 px-2.5 py-2">
              <span className="rounded bg-amber-300/10 px-1.5 py-px text-[9px] text-amber-200">{p.publish_platform}</span>
              {p.published_at && <span className="text-[10px] text-zinc-400">{fmtTime(p.published_at)}</span>}
              {p.publish_url ? (
                <a
                  href={p.publish_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="min-w-0 flex-1 truncate text-[10px] text-sky-300/90 underline-offset-2 hover:underline"
                >
                  {p.publish_url}
                </a>
              ) : (
                <span className="text-[10px] text-zinc-600">无链接</span>
              )}
              <span className="ml-auto flex shrink-0 items-center gap-2">
                {Object.keys(p.stats ?? {}).length > 0 ? (
                  <span className="max-w-[180px] truncate font-mono text-[9px] text-zinc-500" title={JSON.stringify(p.stats)}>
                    {JSON.stringify(p.stats)}
                  </span>
                ) : (
                  <span className="text-[9px] text-zinc-700">无回流数据</span>
                )}
                <button
                  onClick={() => remove(p.id)}
                  className={`rounded border px-1.5 py-px text-[9px] ${confirmId === p.id ? "border-rose-400/40 bg-rose-400/15 text-rose-200" : "border-white/10 text-zinc-500 hover:text-rose-300"}`}
                >
                  {confirmId === p.id ? "确认删除" : "删除"}
                </button>
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
