import { useCallback, useEffect, useState } from "react";
import { useAuth } from "../auth";
import {
  IconBrain,
  IconCheck,
  IconMemory,
  IconSearch,
  IconTrash,
} from "../components/Icons";

interface MemoryItem {
  id: string;
  type: string;
  content: string;
  importance: number;
  updated_at?: string;
}
interface EntityItem {
  entity: string;
  type: string;
  weight: number;
}
interface Stats {
  preference?: number;
  feedback?: number;
  dream?: number;
  total: number;
}

const TYPE_META: Record<string, { label: string; color: string }> = {
  preference: { label: "偏好", color: "var(--accent)" },
  feedback: { label: "反馈", color: "#30d158" },
  dream: { label: "结晶", color: "#ffb340" },
};

export default function MemoryCenter() {
  const { token } = useAuth() as { token: string | null };
  const [memories, setMemories] = useState<MemoryItem[]>([]);
  const [entities, setEntities] = useState<EntityItem[]>([]);
  const [stats, setStats] = useState<Stats>({ total: 0 });
  const [type, setType] = useState("all");
  const [q, setQ] = useState("");
  const [draft, setDraft] = useState("");
  const [saving, setSaving] = useState(false);
  const [toast, setToast] = useState("");

  const headers = { Authorization: `Bearer ${token}` };

  const load = useCallback(async () => {
    try {
      const res = await fetch("/api/chat/memories", { headers });
      const data = await res.json();
      setMemories(data.memories || []);
      setEntities(data.entities || []);
      setStats(data.stats || { total: 0 });
    } catch {
      /* 静默 */
    }
  }, [token]);

  useEffect(() => {
    load();
  }, [load]);

  const flash = (msg: string) => {
    setToast(msg);
    window.setTimeout(() => setToast(""), 2200);
  };

  const del = async (id: string) => {
    await fetch(`/api/chat/memories/${id}`, { method: "DELETE", headers });
    flash("已忘记");
    load();
  };

  const add = async () => {
    const content = draft.trim();
    if (!content || saving) return;
    setSaving(true);
    const res = await fetch("/api/chat/memories", {
      method: "POST",
      headers: { ...headers, "Content-Type": "application/json" },
      body: JSON.stringify({ content, memory_type: "preference", importance: 5 }),
    });
    setSaving(false);
    if (res.ok) {
      setDraft("");
      flash("记住了");
      load();
    }
  };

  const filtered = memories.filter((m) => {
    if (type !== "all" && m.type !== type) return false;
    if (q.trim() && !m.content.includes(q.trim())) return false;
    return true;
  });

  const typeLabels: Array<{ key: string; label: string; count: number }> = [
    { key: "all", label: "全部", count: stats.total || 0 },
    { key: "preference", label: "偏好", count: stats.preference || 0 },
    { key: "feedback", label: "反馈", count: stats.feedback || 0 },
    { key: "dream", label: "结晶", count: stats.dream || 0 },
  ];

  return (
    <div className="mx-auto max-w-3xl px-4 py-6 md:px-8 md:py-10">
      <div className="anim-fade-up flex items-center gap-3">
        <div className="flex h-11 w-11 items-center justify-center rounded-2xl bg-[var(--accent-soft)] text-[22px] text-[var(--accent)]">
          <IconBrain />
        </div>
        <div>
          <h1 className="text-[22px] font-semibold tracking-tight text-white">记忆中心</h1>
          <p className="mt-0.5 text-[12.5px] text-[var(--text-2)]">
            AI 记住了你什么，你说了算 · 全部存本地，不上云
          </p>
        </div>
      </div>

      {/* 手动添加 */}
      <div className="anim-fade-up mt-6 flex items-center gap-2">
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && add()}
          placeholder="告诉 AI 一件想让它记住的事…"
          className="input min-w-0 flex-1"
        />
        <button onClick={add} disabled={saving} className="btn btn-primary shrink-0">
          <IconCheck /> {saving ? "记住中…" : "让它记住"}
        </button>
      </div>

      {/* 统计分段控制 */}
      <div className="anim-fade-up mt-6 flex flex-wrap items-center gap-2">
        <div className="flex gap-1 rounded-full bg-[var(--bg-card)] p-1">
          {typeLabels.map((t) => (
            <button
              key={t.key}
              onClick={() => setType(t.key)}
              className={`rounded-full px-3.5 py-1.5 text-[12.5px] transition-all ${
                type === t.key
                  ? "bg-[var(--accent)] font-medium text-white shadow-[0_2px_8px_rgba(10,132,255,0.35)]"
                  : "text-[var(--text-2)] hover:text-[var(--text-1)]"
              }`}
            >
              {t.label}
              <span className={type === t.key ? "ml-1 opacity-80" : "ml-1 text-[var(--text-3)]"}>
                {t.count}
              </span>
            </button>
          ))}
        </div>
        <div className="ml-auto flex items-center gap-2">
          <IconSearch className="text-[15px] text-[var(--text-3)]" />
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="搜索记忆…"
            className="input w-44 !py-1.5 text-[13px]"
          />
        </div>
      </div>

      {/* 记忆列表 */}
      <div className="mt-4 space-y-2">
        {filtered.length === 0 && (
          <div className="card flex flex-col items-center gap-2 py-12 text-[var(--text-3)]">
            <IconMemory className="text-[30px] opacity-60" />
            <span className="text-[13px]">
              {memories.length === 0 ? "还没有记忆，和 AI 聊几句就会自动沉淀" : "没有匹配的记忆"}
            </span>
          </div>
        )}
        {filtered.map((m, i) => {
          const meta = TYPE_META[m.type] || { label: "记忆", color: "var(--text-2)" };
          return (
            <div
              key={m.id}
              className="anim-fade-up group flex items-start gap-3 rounded-[16px] border border-[var(--separator-soft)] bg-[var(--bg-card)] px-4 py-3.5 shadow-[var(--shadow-card)] transition-colors hover:border-[var(--separator)]"
              style={{ animationDelay: `${Math.min(i * 30, 300)}ms` }}
            >
              <span
                className="mt-1 h-2 w-2 shrink-0 rounded-full"
                style={{ background: meta.color }}
              />
              <div className="min-w-0 flex-1">
                <div className="text-[14px] leading-relaxed text-[var(--text-1)]">{m.content}</div>
                <div className="mt-1.5 flex items-center gap-2.5 text-[11px] text-[var(--text-3)]">
                  <span style={{ color: meta.color }}>{meta.label}</span>
                  <span className="tracking-tight">
                    {"★".repeat(Math.max(1, Math.min(Math.round(m.importance / 2), 5))).padEnd(5, "☆")}
                  </span>
                  {m.updated_at && <span>{m.updated_at.slice(5, 16)}</span>}
                </div>
              </div>
              <button
                onClick={() => del(m.id)}
                className="btn btn-ghost btn-sm shrink-0 !text-[var(--text-3)] opacity-0 transition-all hover:!bg-[rgba(255,69,58,0.12)] hover:!text-[var(--danger)] group-hover:opacity-100"
                title="删除这条记忆"
              >
                <IconTrash /> 忘记
              </button>
            </div>
          );
        })}
      </div>

      {/* 实体图谱 */}
      {entities.length > 0 && (
        <div className="anim-fade-up mt-8">
          <h2 className="mb-3 flex items-center gap-2 text-[13px] font-medium text-[var(--text-2)]">
            <IconBrain className="text-[15px] text-[var(--accent)]" />
            你的兴趣关系网（AI 根据记忆自动关联）
          </h2>
          <div className="flex flex-wrap gap-2">
            {entities.map((e) => (
              <span key={`${e.entity}-${e.type}`} className="chip !text-[12px]">
                <span
                  className="h-1.5 w-1.5 rounded-full"
                  style={{ background: e.type === "platform" ? "var(--accent)" : e.type === "city" ? "#30d158" : e.type === "video_type" ? "#ffb340" : "var(--text-3)" }}
                />
                {e.entity}
                <span className="text-[var(--text-3)]">
                  {e.type === "platform" ? "平台" : e.type === "city" ? "城市" : e.type === "video_type" ? "类型" : e.type === "style" ? "风格" : e.type}
                </span>
              </span>
            ))}
          </div>
        </div>
      )}

      {toast && (
        <div className="anim-pop fixed bottom-6 left-1/2 z-50 -translate-x-1/2 rounded-full bg-[var(--bg-card-hi)] px-4 py-2 text-[13px] text-white shadow-[var(--shadow-float)]">
          {toast}
        </div>
      )}
    </div>
  );
}
