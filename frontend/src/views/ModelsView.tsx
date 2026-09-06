import { useCallback, useEffect, useState } from "react";
import { api, type AiProviderCard } from "../api";

const KIND_OPTIONS = ["pro", "vision", "flash", "other"] as const;
const KIND_LABEL: Record<string, string> = {
  pro: "统一拆解档（多模态五层）",
  vision: "画面理解 / 多模态",
  flash: "备用（快速问答）",
  other: "自定义模型",
};

/* ---------------- 接入 / 编辑表单 ---------------- */
function ProviderForm({
  initial,
  onSaved,
  onCancel,
}: {
  initial: AiProviderCard | null;
  onSaved: (c: AiProviderCard) => void;
  onCancel: () => void;
}) {
  const isEdit = !!initial;
  const [name, setName] = useState(initial?.name ?? "");
  const [baseUrl, setBaseUrl] = useState(initial?.base_url ?? "");
  const [apiKey, setApiKey] = useState("");
  const [keySet, setKeySet] = useState(initial?.api_key_set ?? false);
  const [priority, setPriority] = useState(initial?.priority ?? 10);
  const [topupUrl, setTopupUrl] = useState(initial?.topup_url ?? "");
  const [brandColor, setBrandColor] = useState(initial?.brand_color ?? "#888");
  const [models, setModels] = useState<Array<{ id: string; kind: string; label?: string }>>(
    initial && initial.models.length > 0
      ? initial.models.map((m) => ({ id: m.id, kind: m.kind ?? "pro", label: m.label }))
      : [{ id: "", kind: "pro" }, { id: "", kind: "vision" }]
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  function addRow() {
    setModels((s) => [...s, { id: "", kind: "pro" }]);
  }
  function patchRow(i: number, patch: Partial<{ id: string; kind: string; label?: string }>) {
    setModels((s) => s.map((r, idx) => (idx === i ? { ...r, ...patch } : r)));
  }
  function removeRow(i: number) {
    setModels((s) => s.filter((_, idx) => idx !== i));
  }

  async function save() {
    const trimmed = models
      .map((m) => ({ ...m, id: m.id.trim() }))
      .filter((m) => m.id.length > 0);
    if (!name.trim() || !baseUrl.trim() || trimmed.length === 0) {
      setError("请填写服务商名称、base_url，并至少保留一个模型 ID");
      return;
    }
    if (!isEdit && !apiKey.trim()) {
      setError("首次接入必须填写 api-key");
      return;
    }
    if (isEdit && !keySet && !apiKey.trim()) {
      setError("当前服务商未配置 key，本次必须填写 api-key");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const payload = {
        key: initial?.key ?? name.trim().toLowerCase().replace(/\s+/g, "-"),
        name: name.trim(),
        base_url: baseUrl.trim(),
        api_key: apiKey.trim() || undefined,
        models: trimmed,
        priority,
        topup_url: topupUrl.trim() || undefined,
        brand_color: brandColor.trim() || undefined,
      };
      const saved = isEdit ? await api.aiProviderUpdate(initial!.key, payload) : await api.aiProviderCreate(payload as never);
      onSaved(saved);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="rounded-xl border border-white/10 bg-[#1c1f26] p-5">
      <div className="mb-4 flex items-center justify-between">
        <h3 className="text-sm font-semibold text-zinc-100">{isEdit ? `编辑 · ${initial!.name}` : initial ? `接入 ${initial.name}` : "接入新 AI 服务商"}</h3>
        <button onClick={onCancel} className="text-xs text-zinc-500 hover:text-zinc-300">取消</button>
      </div>

      <div className="grid gap-3 md:grid-cols-2">
        <label className="block">
          <span className="text-[11px] uppercase tracking-wider text-zinc-500">服务商名（如 DeepSeek）</span>
          <input value={name} onChange={(e) => setName(e.target.value)} className="mt-1 w-full rounded-lg border border-white/10 bg-black/20 px-3 py-2 text-sm text-zinc-200 outline-none focus:border-amber-300/50" placeholder="DeepSeek" />
        </label>
        <label className="block">
          <span className="text-[11px] uppercase tracking-wider text-zinc-500">Base URL（OpenAI 兼容）</span>
          <input value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} className="mt-1 w-full rounded-lg border border-white/10 bg-black/20 px-3 py-2 font-mono text-sm text-zinc-200 outline-none focus:border-amber-300/50" placeholder="https://api.deepseek.com/v1" />
        </label>
        <label className="block md:col-span-2">
          <span className="text-[11px] uppercase tracking-wider text-zinc-500">
            API Key {isEdit && keySet && !apiKey ? <span className="normal-case text-zinc-600">（已配置 {initial?.api_key_masked}，留空则保持原 key）</span> : ""}
          </span>
          <input type="password" value={apiKey} onChange={(e) => { setApiKey(e.target.value); if (e.target.value) setKeySet(true); }} className="mt-1 w-full rounded-lg border border-white/10 bg-black/20 px-3 py-2 font-mono text-sm text-zinc-200 outline-none focus:border-amber-300/50" placeholder={isEdit && keySet ? "sk-…（留空不变）" : "sk-…"} />
        </label>
      </div>

      <div className="mt-4">
        <div className="mb-2 flex items-center justify-between">
          <span className="text-[11px] uppercase tracking-wider text-zinc-500">模型档位（flash/pro/vision/other）</span>
          <button onClick={addRow} className="text-xs text-amber-300 hover:underline">+ 添加模型</button>
        </div>
        <div className="space-y-2">
          {models.map((m, i) => (
            <div key={i} className="flex flex-wrap items-center gap-2">
              <input
                value={m.id}
                onChange={(e) => patchRow(i, { id: e.target.value })}
                className="flex-1 rounded-lg border border-white/10 bg-black/20 px-3 py-1.5 font-mono text-sm text-zinc-200 outline-none focus:border-amber-300/50"
                placeholder="deepseek-chat / doubao-1-5-pro-32k-250115 …"
              />
              <select
                value={m.kind}
                onChange={(e) => patchRow(i, { kind: e.target.value })}
                className="rounded-lg border border-white/10 bg-black/20 px-2 py-1.5 text-xs text-zinc-300 outline-none"
              >
                {KIND_OPTIONS.map((k) => (
                  <option key={k} value={k}>{k}</option>
                ))}
              </select>
              <input
                value={m.label ?? ""}
                onChange={(e) => patchRow(i, { label: e.target.value })}
                className="w-40 rounded-lg border border-white/10 bg-black/20 px-2 py-1.5 text-xs text-zinc-300 outline-none focus:border-amber-300/50"
                placeholder="备注（可选）"
              />
              <button onClick={() => removeRow(i)} className="text-xs text-zinc-600 hover:text-rose-300">删除</button>
            </div>
          ))}
        </div>
        <p className="mt-1 text-[10px] text-zinc-600">档位说明：pro=五层拆解默认档 · vision=画面理解 · flash=轻量问答 · other=其余按 kind 路由</p>
      </div>

      <div className="mt-4 grid gap-3 md:grid-cols-3">
        <label className="block">
          <span className="text-[11px] uppercase tracking-wider text-zinc-500">路由优先级（小者优先）</span>
          <input type="number" value={priority} onChange={(e) => setPriority(Number(e.target.value))} className="mt-1 w-full rounded-lg border border-white/10 bg-black/20 px-3 py-2 text-sm text-zinc-200 outline-none" />
        </label>
        <label className="block">
          <span className="text-[11px] uppercase tracking-wider text-zinc-500">充值页 URL</span>
          <input value={topupUrl} onChange={(e) => setTopupUrl(e.target.value)} className="mt-1 w-full rounded-lg border border-white/10 bg-black/20 px-3 py-2 text-sm text-zinc-200 outline-none" placeholder="https://…" />
        </label>
        <label className="block">
          <span className="text-[11px] uppercase tracking-wider text-zinc-500">品牌色（卡片角标）</span>
          <input value={brandColor} onChange={(e) => setBrandColor(e.target.value)} className="mt-1 w-full rounded-lg border border-white/10 bg-black/20 px-3 py-2 text-sm text-zinc-200 outline-none" placeholder="#888" />
        </label>
      </div>

      {error && <p className="mt-3 rounded-lg border border-rose-400/20 bg-rose-400/10 px-3 py-2 text-xs text-rose-300">{error}</p>}
      <div className="mt-4 flex justify-end gap-2">
        <button onClick={onCancel} className="rounded-lg border border-white/10 px-4 py-2 text-xs text-zinc-400 hover:bg-white/5">取消</button>
        <button onClick={() => void save()} disabled={busy} className="rounded-lg bg-amber-300 px-4 py-2 text-xs font-medium text-black hover:bg-amber-200 disabled:opacity-50">
          {busy ? "保存中…" : isEdit ? "保存修改" : "接入"}
        </button>
      </div>
    </div>
  );
}

/* ---------------- 已配置服务商卡片 ---------------- */
function ConfiguredCard({
  card,
  onEdit,
  onDelete,
}: {
  card: AiProviderCard;
  onEdit: () => void;
  onDelete: () => void;
}) {
  return (
    <div className="rounded-xl border border-white/5 bg-[#1c1f26] p-4">
      <div className="flex items-start justify-between gap-2">
        <div className="flex min-w-0 items-center gap-2">
          {card.logo_url ? (
            <img src={card.logo_url} alt="" className="h-6 w-6 rounded" />
          ) : (
            <span className="flex h-6 w-6 items-center justify-center rounded text-[11px] font-bold text-black" style={{ background: card.brand_color || "#888" }}>
              {card.name.slice(0, 1)}
            </span>
          )}
          <div className="min-w-0">
            <div className="text-sm font-semibold text-zinc-100">{card.name}</div>
            <div className="truncate font-mono text-[10px] text-zinc-500">
              {card.api_key_set ? card.api_key_masked : "未填 key"} · priority {card.priority}
            </div>
          </div>
        </div>
        <span className={`rounded-full px-2 py-0.5 text-[10px] ${card.api_key_set ? "bg-emerald-400/10 text-emerald-300" : "bg-white/5 text-zinc-500"}`}>
          {card.api_key_set ? "已配置" : "待填 key"}
        </span>
      </div>

      <div className="mt-3 space-y-1">
        {card.models.map((m) => (
          <div key={m.id} className="flex items-center justify-between rounded-lg bg-white/[0.04] px-2 py-1.5">
            <span className="font-mono text-[11px] text-zinc-300">{m.id}</span>
            <span className="ml-2 rounded bg-white/5 px-1.5 py-0.5 text-[9px] text-zinc-500">{m.kind}</span>
          </div>
        ))}
      </div>

      <div className="mt-3 flex items-center justify-end gap-2">
        <button onClick={onDelete} className="rounded-lg border border-white/10 px-2 py-1 text-[11px] text-rose-300/80 hover:bg-rose-400/10">删除</button>
        <button onClick={onEdit} className="rounded-lg border border-white/10 px-3 py-1 text-[11px] text-zinc-300 hover:bg-white/5">编辑 / 换 key</button>
      </div>
    </div>
  );
}

/* ---------------- 模板按钮 ---------------- */
function TemplateTile({
  tpl,
  onPick,
}: {
  tpl: AiProviderCard;
  onPick: () => void;
}) {
  return (
    <button
      onClick={onPick}
      className="flex flex-col items-start gap-2 rounded-xl border border-white/5 bg-[#1c1f26] p-4 text-left transition-colors hover:border-amber-300/40"
    >
      <div className="flex w-full items-center justify-between">
        <span className="text-sm font-medium text-zinc-100">{tpl.name}</span>
        {tpl.logo_url ? (
          <img src={tpl.logo_url} alt="" className="h-5 w-5 rounded" />
        ) : (
          <span className="flex h-5 w-5 items-center justify-center rounded text-[10px] font-bold text-black" style={{ background: tpl.brand_color || "#888" }}>
            {tpl.name.slice(0, 1)}
          </span>
        )}
      </div>
      <div className="font-mono text-[10px] text-zinc-500">{tpl.base_url}</div>
      <div className="mt-1 flex flex-wrap gap-1">
        {tpl.models.slice(0, 5).map((m) => (
          <span key={m.id} className="rounded bg-white/5 px-1.5 py-0.5 font-mono text-[9px] text-zinc-400">{m.id}</span>
        ))}
      </div>
      <div className="mt-1 text-[10px] text-amber-300/80">填写 api-key 接入 →</div>
    </button>
  );
}

/* ---------------- 主视图 ---------------- */
export default function ModelsView() {
  const [cards, setCards] = useState<AiProviderCard[]>([]);
  const [templates, setTemplates] = useState<AiProviderCard[]>([]);
  const [formOpen, setFormOpen] = useState(false);
  const [editing, setEditing] = useState<AiProviderCard | null>(null);
  const [template, setTemplate] = useState<AiProviderCard | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    try {
      const data = await api.aiProviders();
      setCards(data.items ?? []);
      setTemplates(data.templates ?? []);
      setError("");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    void (async () => {
      setLoading(true);
      await load();
      setLoading(false);
    })();
  }, [load]);

  function openAdd(tpl?: AiProviderCard) {
    setEditing(null);
    setTemplate(tpl ?? null);
    setFormOpen(true);
  }
  function openEdit(c: AiProviderCard) {
    setEditing(c);
    setTemplate(null);
    setFormOpen(true);
  }
  function handleSaved(_c: AiProviderCard) {
    setFormOpen(false);
    setEditing(null);
    setTemplate(null);
    void load();
  }
  async function remove(c: AiProviderCard) {
    if (!window.confirm(`确认删除 ${c.name}？仅移除本地配置，不影响厂商账户。`)) return;
    try {
      await api.aiProviderRemove(c.key);
      void load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  return (
    <div className="mx-auto max-w-6xl px-4 py-6 lg:px-8">
      <header className="mb-5 flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-zinc-100">模型管理</h1>
          <p className="mt-1 text-sm text-zinc-500">
            预置各家 OpenAI 兼容接口模板，填 api-key 即用；AI 用量页按「已配置服务商」动态展示卡片
          </p>
        </div>
        <button onClick={() => openAdd()} className="rounded-lg bg-amber-300 px-4 py-2 text-sm font-medium text-black hover:bg-amber-200">
          + 手动接入服务商
        </button>
      </header>

      {error && <p className="mb-4 rounded-lg border border-rose-400/20 bg-rose-400/10 px-3 py-2 text-sm text-rose-300">{error}</p>}
      {loading && <p className="text-sm text-zinc-500">加载中…</p>}

      {/* 已配置 */}
      {cards.length > 0 && (
        <>
          <div className="mb-3 text-xs font-medium uppercase tracking-wider text-zinc-500">已接入（{cards.length}）</div>
          <div className="mb-6 grid gap-4 md:grid-cols-2 xl:grid-cols-3">
            {cards.map((c) => (
              <ConfiguredCard key={c.key} card={c} onEdit={() => openEdit(c)} onDelete={() => void remove(c)} />
            ))}
          </div>
        </>
      )}

      {/* 待接入模板 */}
      <div className="mb-3 text-xs font-medium uppercase tracking-wider text-zinc-500">可接入模板（{templates.length}）</div>
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        {templates.map((t) => (
          <TemplateTile key={t.key} tpl={t} onPick={() => openAdd(t)} />
        ))}
        {templates.length === 0 && (
          <div className="rounded-xl border border-dashed border-white/10 p-8 text-center text-sm text-zinc-600 md:col-span-2 xl:col-span-3">
            暂无可接入模板，可点右上角「手动接入服务商」自定义
          </div>
        )}
      </div>

      {formOpen && (
        <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/60 p-4 md:p-10">
          <div className="w-full max-w-2xl">
            <ProviderForm
              initial={editing ?? template}
              onSaved={handleSaved}
              onCancel={() => { setFormOpen(false); setEditing(null); setTemplate(null); }}
            />
          </div>
        </div>
      )}
    </div>
  );
}
