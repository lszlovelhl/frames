import { useEffect, useMemo, useState } from "react";
import { api, type ProductInfo } from "../api";

const CATEGORY_ORDER = [
  "知识口播", "剧情短剧", "美食", "搞笑", "美妆", "萌宠", "游戏", "音乐舞蹈", "运动健身", "情感",
  "生活记录", "科技数码", "财经职场", "汽车出行", "文旅非遗", "综艺娱乐", "亲子育儿", "影视解说", "时尚穿搭", "好物测评",
];
const UNKNOWN_CATEGORY = "未分类";

const INDUSTRY_ICONS: Record<string, string> = {};

interface Props {
  onCreateWithProducts?: (productIds: string[]) => void;
}

interface SpecRow {
  k: string;
  v: string;
}

interface SellingRow {
  title: string;
  detail: string;
}

const EMPTY_SPEC: SpecRow = { k: "", v: "" };
const EMPTY_SELLING: SellingRow = { title: "", detail: "" };

export default function ProductsView({ onCreateWithProducts }: Props) {
  const [list, setList] = useState<ProductInfo[]>([]);
  const [industries, setIndustries] = useState<string[]>([]);
  const [industry, setIndustry] = useState("all");
  const [category, setCategory] = useState("all");
  const [q, setQ] = useState("");
  const [debouncedQ, setDebouncedQ] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [selectionMode, setSelectionMode] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [createOpen, setCreateOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [autofilling, setAutofilling] = useState(false);
  const [autoMsg, setAutoMsg] = useState("");

  // 新建表单草稿
  const [formIndustry, setFormIndustry] = useState("");
  const [formTags, setFormTags] = useState("");
  const [formBrand, setFormBrand] = useState("");
  const [formName, setFormName] = useState("");
  const [formSeries, setFormSeries] = useState("");
  const [formHeadline, setFormHeadline] = useState("");
  const [formPrice, setFormPrice] = useState("");
  const [formSpecs, setFormSpecs] = useState<SpecRow[]>([EMPTY_SPEC]);
  const [formSellings, setFormSellings] = useState<SellingRow[]>([EMPTY_SELLING]);

  useEffect(() => {
    const t = setTimeout(() => setDebouncedQ(q.trim()), 350);
    return () => clearTimeout(t);
  }, [q]);

  async function load() {
    try {
      setLoading(true);
      const data = await api.listProducts({ industry, category, q: debouncedQ || undefined });
      setList(data.items ?? []);
      setIndustries(data.industries ?? []);
      setError("");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
  }, [industry, category, debouncedQ]);

  // 赛道聚合：当前列表所有 product.category_tags 并按权威排序
  const categoryOptions = useMemo(() => {
    const seen = new Map<string, string>();
    for (const p of list) {
      for (const t of p.category_tags ?? []) {
        if (!seen.has(t)) seen.set(t, t);
      }
    }
    const rank = (k: string) => {
      if (k === UNKNOWN_CATEGORY) return 998;
      const i = CATEGORY_ORDER.indexOf(k);
      return i < 0 ? 997 : i;
    };
    return [...seen.entries()].sort((a, b) => rank(a[0]) - rank(b[0]));
  }, [list]);

  const shown = category === "all" ? list : list.filter((p) => (p.category_tags ?? []).includes(category));

  // 行业分组后的显示顺序（按 industries 权威顺序 + 其他兜底）
  const grouped = useMemo(() => {
    const order = new Map(industries.map((x, i) => [x, i]));
    const map = new Map<string, ProductInfo[]>();
    for (const p of shown) {
      const arr = map.get(p.industry) ?? [];
      arr.push(p);
      map.set(p.industry, arr);
    }
    return [...map.entries()].sort((a, b) => {
      const ai = order.get(a[0]) ?? 999;
      const bi = order.get(b[0]) ?? 999;
      return ai - bi || (a[0] < b[0] ? -1 : 1);
    });
  }, [shown, industries]);

  function toggleSelect(id: string) {
    setSelected((prev) => {
      const s = new Set(prev);
      if (s.has(id)) s.delete(id);
      else s.add(id);
      return s;
    });
  }

  function productShort(p: ProductInfo): string {
    return `${p.brand} ${p.name}`;
  }

  function goCreate(ids: string[]) {
    onCreateWithProducts?.(ids);
  }

  async function runAutofill() {
    if (!formIndustry || !formBrand.trim() || !formName.trim()) {
      setAutoMsg("先填行业、品牌和产品名，才能 AI 补全");
      return;
    }
    try {
      setAutofilling(true);
      setAutoMsg("AI 生成中…");
      const d = await api.autofillProduct({
        industry: formIndustry,
        brand: formBrand,
        name: formName,
        series: formSeries.trim() || null,
        price_range: formPrice.trim() || null,
      });
      setFormSeries(d.series ?? "");
      setFormHeadline(d.headline ?? "");
      setFormPrice(d.price_range ?? "");
      setFormTags((d.category_tags ?? []).join("、"));
      setFormSpecs(
        d.specs && d.specs.length > 0 ? d.specs.map((s) => ({ ...s })) : [EMPTY_SPEC]
      );
      setFormSellings(
        d.selling_points && d.selling_points.length > 0 ? d.selling_points.map((s) => ({ ...s })) : [EMPTY_SELLING]
      );
      setAutoMsg("AI 生成仅供参考：参数与卖点可能不实，请核对修改后再保存");
    } catch (e) {
      setAutoMsg(e instanceof Error ? e.message : String(e));
    } finally {
      setAutofilling(false);
    }
  }

  async function submitCreate() {
    try {
      setSubmitting(true);
      const category_tags = formTags
        .split(/[,，、\s]+/)
        .map((s) => s.trim())
        .filter(Boolean);
      await api.createProduct({
        industry: formIndustry,
        category_tags,
        brand: formBrand,
        name: formName,
        series: formSeries || null,
        headline: formHeadline,
        price_range: formPrice || null,
        specs: formSpecs.filter((s) => s.k.trim() && s.v.trim()),
        selling_points: formSellings.filter((s) => s.title.trim()),
      });
      setCreateOpen(false);
      resetForm();
      await load();
      setError("");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSubmitting(false);
    }
  }

  function resetForm() {
    setFormIndustry("");
    setFormTags("");
    setFormBrand("");
    setFormName("");
    setFormSeries("");
    setFormHeadline("");
    setFormPrice("");
    setFormSpecs([EMPTY_SPEC]);
    setFormSellings([EMPTY_SELLING]);
    setAutoMsg("");
  }

  const selectedItems = list.filter((p) => selected.has(p.id));
  const inputCls =
    "w-full rounded-lg border border-white/10 bg-[#1c1f26] px-2.5 py-1.5 text-sm text-zinc-200 placeholder-zinc-600 outline-none focus:border-amber-300/40";

  return (
    <div className="mx-auto max-w-4xl px-4 py-6 lg:px-8">
      <header className="mb-5 flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-zinc-100">产品库</h1>
          <p className="mt-1 text-sm text-zinc-500">主流产品参数与卖点 · 按行业 / 内容赛道检索 · 创作台可直接带入种草</p>
        </div>
        <div className="flex items-center gap-2">
          <span className="rounded-full border border-amber-300/20 bg-amber-300/10 px-3 py-1 text-xs text-amber-200">
            {list.length} 个产品
          </span>
          {!selectionMode && (
            <button
              onClick={() => setCreateOpen(true)}
              className="rounded-lg border border-amber-300/20 bg-amber-300/10 px-3 py-1.5 text-xs text-amber-200 hover:bg-amber-300/20"
            >
              + 新产品
            </button>
          )}
          <button
            onClick={() => {
              setSelectionMode(false);
              setSelected(new Set());
              void load();
            }}
            className="rounded-lg border border-white/10 px-3 py-1.5 text-xs text-zinc-400 hover:bg-white/5"
          >
            刷新
          </button>
        </div>
      </header>

      {error && <p className="mb-4 rounded-lg border border-rose-400/20 bg-rose-400/10 px-3 py-2 text-sm text-rose-300">{error}</p>}

      {/* 多选操作条 */}
      {selectionMode && (
        <div className="sticky top-0 z-20 -mx-1 mb-4 flex flex-wrap items-center gap-2 rounded-xl border border-amber-300/20 bg-[#191c21]/95 px-3 py-2 shadow-lg backdrop-blur">
          <span className="text-xs text-zinc-300">
            已选 <span className="font-semibold text-amber-300">{selected.size}</span> 个产品
          </span>
          {selectedItems.length > 0 && (
            <span className="max-w-[240px] truncate text-[11px] text-zinc-500">
              {selectedItems.map((p) => productShort(p)).join("、")}
            </span>
          )}
          <div className="ml-auto flex items-center gap-2">
            <button
              disabled={selected.size < 1 || !onCreateWithProducts}
              onClick={() => selected.size >= 1 && goCreate([...selected])}
              className="rounded-lg border border-emerald-300/20 bg-emerald-400/10 px-3 py-1.5 text-xs text-emerald-200 hover:bg-emerald-400/20 disabled:cursor-not-allowed disabled:opacity-40"
              title="带着选中的产品跳去创作台生成脚本"
            >
              去创作台 ✎
            </button>
            <button
              onClick={() => {
                setSelectionMode(false);
                setSelected(new Set());
              }}
              className="rounded-lg border border-white/10 px-3 py-1.5 text-xs text-zinc-400 hover:bg-white/5"
            >
              取消
            </button>
          </div>
        </div>
      )}

      {/* 过滤行 */}
      <div className="mb-5 flex flex-wrap gap-2">
        <select value={industry} onChange={(e) => setIndustry(e.target.value)} className="rounded-lg border border-white/10 bg-[#1c1f26] px-2.5 py-1.5 text-sm text-zinc-300 outline-none">
          <option value="all">全部行业</option>
          {industries.map((c) => (
            <option key={c} value={c}>{c}</option>
          ))}
        </select>
        <select value={category} onChange={(e) => setCategory(e.target.value)} className="rounded-lg border border-white/10 bg-[#1c1f26] px-2.5 py-1.5 text-sm text-zinc-300 outline-none">
          <option value="all">全部赛道</option>
          {categoryOptions.map(([key]) => (
            <option key={key} value={key}>{key}</option>
          ))}
        </select>
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="搜品牌 / 产品 / 卖点 / 参数…"
          className="min-w-0 flex-1 rounded-lg border border-white/10 bg-[#1c1f26] px-3 py-1.5 text-sm text-zinc-200 placeholder-zinc-600 outline-none focus:border-amber-300/30"
        />
      </div>

      {loading && <div className="text-sm text-zinc-500">加载中…</div>}
      {!loading && shown.length === 0 && (
        <div className="rounded-xl border border-dashed border-white/10 p-8 text-center text-sm text-zinc-500">
          没有符合条件的产品，可以点右上角「+ 新产品」补充
        </div>
      )}

      {!loading && grouped.map(([ind, items]) => (
        <section key={ind} className="mb-6">
          <h2 className="mb-2 flex items-center gap-2 text-sm font-medium text-zinc-400">
            <span className="text-base">{INDUSTRY_ICONS[ind] ?? "▧"}</span>
            {ind}
            <span className="text-xs font-normal text-zinc-600">{items.length}</span>
          </h2>
          <div className="flex flex-col gap-2.5">
            {items.map((p) => {
              const expanded = expandedId === p.id;
              return (
                <div
                  key={p.id}
                  className={`rounded-xl border bg-[#1c1f26] transition ${expanded ? "border-amber-300/25" : "border-white/5 hover:border-white/10"}`}
                >
                  {/* 摘要行 */}
                  <button
                    onClick={() => setExpandedId(expanded ? null : p.id)}
                    className="flex w-full items-start gap-3 px-3.5 py-3 text-left"
                  >
                    {selectionMode && (
                      <span
                        onClick={(e) => {
                          e.stopPropagation();
                          toggleSelect(p.id);
                        }}
                        className={`mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded border text-[10px] ${selected.has(p.id) ? "border-amber-300 bg-amber-300/30 text-amber-100" : "border-white/25 text-transparent"}`}
                      >
                        ✓
                      </span>
                    )}
                    <span className="min-w-0 flex-1">
                      <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
                        <span className="text-sm font-semibold text-zinc-100">{p.brand}</span>
                        <span className="text-sm font-medium text-zinc-200">{p.name}</span>
                        {p.price_range && <span className="text-[11px] text-zinc-500">{p.price_range}</span>}
                      </span>
                      <span className="mt-0.5 block truncate text-xs text-zinc-500">
                        {p.series && p.series !== p.name && <span className="text-zinc-600">{p.series} · </span>}
                        {p.headline}
                      </span>
                      <span className="mt-1.5 flex flex-wrap gap-1">
                        {(p.category_tags ?? []).map((t) => (
                          <span key={t} className="rounded bg-violet-400/10 px-1.5 py-0.5 text-[10px] text-violet-300/90">{t}</span>
                        ))}
                        {(p.selling_points ?? []).slice(0, 2).map((s) => (
                          <span key={s.title} className="rounded bg-amber-300/10 px-1.5 py-0.5 text-[10px] text-amber-200/90">{s.title}</span>
                        ))}
                      </span>
                    </span>
                    <span className="mt-0.5 shrink-0 text-[10px] text-zinc-600">{expanded ? "收起 ▴" : "详情 ▾"}</span>
                  </button>

                  {/* 展开详情 */}
                  {expanded && (
                    <div className="border-t border-white/5 px-3.5 py-3">
                      <div className="grid gap-3 sm:grid-cols-2">
                        <div>
                          <h4 className="mb-1.5 text-[11px] font-medium text-zinc-500">核心参数</h4>
                          <div className="flex flex-col gap-1">
                            {(p.specs ?? []).map((s) => (
                              <div key={s.k} className="flex gap-2 text-xs">
                                <span className="w-16 shrink-0 text-zinc-500">{s.k}</span>
                                <span className="text-zinc-300">{s.v}</span>
                              </div>
                            ))}
                            {(p.specs ?? []).length === 0 && <span className="text-xs text-zinc-600">暂无参数</span>}
                          </div>
                        </div>
                        <div>
                          <h4 className="mb-1.5 text-[11px] font-medium text-zinc-500">种草卖点</h4>
                          <div className="flex flex-col gap-1.5">
                            {(p.selling_points ?? []).map((s) => (
                              <div key={s.title} className="text-xs leading-relaxed">
                                <span className="font-medium text-amber-200/90">{s.title}</span>
                                {s.detail && <span className="text-zinc-500"> — {s.detail}</span>}
                              </div>
                            ))}
                            {(p.selling_points ?? []).length === 0 && <span className="text-xs text-zinc-600">暂无卖点</span>}
                          </div>
                        </div>
                      </div>
                      <div className="mt-3 flex items-center justify-end gap-2">
                        {!selectionMode && (
                          <button
                            onClick={() => {
                              setSelectionMode(true);
                              setSelected(new Set([p.id]));
                            }}
                            className="rounded-lg border border-white/10 px-2.5 py-1 text-[11px] text-zinc-400 hover:border-amber-300/30 hover:text-amber-300"
                          >
                            组合带入
                          </button>
                        )}
                        {onCreateWithProducts && (
                          <button
                            onClick={() => goCreate([p.id])}
                            className="rounded-lg border border-emerald-300/20 bg-emerald-400/10 px-2.5 py-1 text-[11px] text-emerald-200 hover:bg-emerald-400/20"
                          >
                            带去创作台 ✎
                          </button>
                        )}
                      </div>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </section>
      ))}

      {/* 新建产品弹窗 */}
      {createOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4" onClick={() => setCreateOpen(false)}>
          <div className="flex max-h-[88vh] w-full max-w-lg flex-col rounded-2xl border border-white/10 bg-[#191c21]" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between border-b border-white/5 px-4 py-3">
              <h3 className="text-sm font-semibold text-zinc-100">新增产品</h3>
              <div className="flex items-center gap-2">
                <button
                  onClick={() => void runAutofill()}
                  disabled={autofilling || !formIndustry || !formBrand.trim() || !formName.trim()}
                  className="rounded-lg border border-amber-300/30 bg-amber-300/10 px-2.5 py-1 text-[11px] font-medium text-amber-200 hover:bg-amber-300/20 disabled:cursor-not-allowed disabled:opacity-40"
                >
                  {autofilling ? "AI 生成中…" : "✦ AI 一键补全"}
                </button>
                <button onClick={() => setCreateOpen(false)} className="text-zinc-500 hover:text-zinc-300">×</button>
              </div>
            </div>
            {autoMsg && (
              <div className="border-b border-amber-300/10 bg-amber-300/5 px-4 py-1.5 text-[11px] text-amber-200/80">{autoMsg}</div>
            )}
            <div className="flex-1 space-y-2.5 overflow-y-auto px-4 py-3">
              <div className="grid grid-cols-2 gap-2">
                <select value={formIndustry} onChange={(e) => setFormIndustry(e.target.value)} className={inputCls}>
                  <option value="">行业…</option>
                  {industries.map((c) => (
                    <option key={c} value={c}>{c}</option>
                  ))}
                </select>
                <input value={formTags} onChange={(e) => setFormTags(e.target.value)} placeholder="适用赛道，逗号分隔（如 好物测评/科技数码）" className={inputCls} />
              </div>
              <div className="grid grid-cols-2 gap-2">
                <input value={formBrand} onChange={(e) => setFormBrand(e.target.value)} placeholder="品牌（必填）" className={inputCls} />
                <input value={formName} onChange={(e) => setFormName(e.target.value)} placeholder="产品名（必填，如 SU7）" className={inputCls} />
              </div>
              <div className="grid grid-cols-2 gap-2">
                <input value={formSeries} onChange={(e) => setFormSeries(e.target.value)} placeholder="系列/定位（选填）" className={inputCls} />
                <input value={formPrice} onChange={(e) => setFormPrice(e.target.value)} placeholder="价格带（如 21.59 万起）" className={inputCls} />
              </div>
              <textarea value={formHeadline} onChange={(e) => setFormHeadline(e.target.value)} placeholder="一句话种草点（必填）" rows={2} className={inputCls + " resize-none"} />

              <div className="pt-1">
                <div className="mb-1 flex items-center justify-between">
                  <span className="text-[11px] font-medium text-zinc-500">核心参数</span>
                  <button onClick={() => setFormSpecs((v) => [...v, { ...EMPTY_SPEC }])} className="text-[11px] text-amber-300 hover:text-amber-200">+ 参数</button>
                </div>
                <div className="space-y-1.5">
                  {formSpecs.map((s, i) => (
                    <div key={i} className="flex items-center gap-1.5">
                      <input value={s.k} onChange={(e) => setFormSpecs((v) => v.map((x, j) => (j === i ? { ...x, k: e.target.value } : x)))} placeholder="参数名（如 续航）" className={inputCls + " flex-1"} />
                      <input value={s.v} onChange={(e) => setFormSpecs((v) => v.map((x, j) => (j === i ? { ...x, v: e.target.value } : x)))} placeholder="值（如 700km）" className={inputCls + " flex-1"} />
                      <button onClick={() => setFormSpecs((v) => v.filter((_, j) => j !== i))} className="shrink-0 text-zinc-600 hover:text-rose-300">×</button>
                    </div>
                  ))}
                </div>
              </div>

              <div className="pt-1">
                <div className="mb-1 flex items-center justify-between">
                  <span className="text-[11px] font-medium text-zinc-500">种草卖点</span>
                  <button onClick={() => setFormSellings((v) => [...v, { ...EMPTY_SELLING }])} className="text-[11px] text-amber-300 hover:text-amber-200">+ 卖点</button>
                </div>
                <div className="space-y-1.5">
                  {formSellings.map((s, i) => (
                    <div key={i} className="flex items-center gap-1.5">
                      <input value={s.title} onChange={(e) => setFormSellings((v) => v.map((x, j) => (j === i ? { ...x, title: e.target.value } : x)))} placeholder="卖点标题" className={inputCls + " w-1/3 shrink-0"} />
                      <input value={s.detail} onChange={(e) => setFormSellings((v) => v.map((x, j) => (j === i ? { ...x, detail: e.target.value } : x)))} placeholder="一句话展开（选填）" className={inputCls + " flex-1"} />
                      <button onClick={() => setFormSellings((v) => v.filter((_, j) => j !== i))} className="shrink-0 text-zinc-600 hover:text-rose-300">×</button>
                    </div>
                  ))}
                </div>
              </div>
            </div>
            <div className="flex items-center justify-end gap-2 border-t border-white/5 px-4 py-3">
              <button onClick={() => setCreateOpen(false)} className="rounded-lg border border-white/10 px-3 py-1.5 text-xs text-zinc-400 hover:bg-white/5">取消</button>
              <button
                onClick={() => void submitCreate()}
                disabled={submitting || !formIndustry || !formBrand.trim() || !formName.trim() || !formHeadline.trim()}
                className="rounded-lg bg-amber-300/90 px-4 py-1.5 text-xs font-medium text-[#14161a] hover:bg-amber-300 disabled:cursor-not-allowed disabled:opacity-40"
              >
                {submitting ? "保存中…" : "保存产品"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
