import { useMemo, useState } from "react";

interface MemoryItem {
  id: string;
  type: string;
  content: string;
  importance: number;
  updated_at?: string;
}

const TYPE_META: Record<string, { label: string; icon: string }> = {
  preference: { label: "偏好", icon: "💡" },
  feedback: { label: "反馈", icon: "🔧" },
  dream: { label: "结晶", icon: "💭" },
};

interface Props {
  memories: MemoryItem[];
  onDelete: (id: string) => void;
  onClose: () => void;
}

export default function MemoryManager({ memories, onDelete, onClose }: Props) {
  const [q, setQ] = useState("");
  const [typeFilter, setTypeFilter] = useState<string>("all");

  const filtered = useMemo(() => {
    let list = memories;
    if (typeFilter !== "all") list = list.filter((m) => m.type === typeFilter);
    if (q.trim()) {
      const kw = q.trim().toLowerCase();
      list = list.filter((m) => m.content.toLowerCase().includes(kw));
    }
    return list;
  }, [memories, q, typeFilter]);

  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/60 md:items-center" onClick={onClose}>
      <div
        className="flex h-[85vh] w-full max-w-2xl flex-col rounded-t-2xl border border-white/10 bg-[#1c2027] md:h-[75vh] md:rounded-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        {/* 头部 */}
        <div className="flex items-center justify-between border-b border-white/5 px-5 py-4">
          <div>
            <h2 className="text-base font-semibold text-zinc-100">🧠 AI 记得你</h2>
            <p className="mt-0.5 text-xs text-zinc-500">
              共 {memories.length} 条记忆 · 点删除即可让我忘记 · 全部存本地不上云
            </p>
          </div>
          <button
            onClick={onClose}
            className="rounded-lg bg-white/5 px-3 py-1.5 text-sm text-zinc-300 hover:bg-white/10"
          >
            关闭
          </button>
        </div>

        {/* 搜索 + 筛选 */}
        <div className="border-b border-white/5 px-5 py-3">
          <div className="flex flex-wrap items-center gap-2">
            <input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="搜索记忆内容…"
              className="min-w-0 flex-1 rounded-lg border border-white/10 bg-[#252932] px-3 py-2 text-sm text-zinc-200 placeholder-zinc-500 outline-none focus:border-amber-400/50"
            />
            <div className="flex gap-1">
              {[
                { key: "all", label: "全部" },
                { key: "preference", label: "💡 偏好" },
                { key: "feedback", label: "🔧 反馈" },
                { key: "dream", label: "💭 结晶" },
              ].map((t) => (
                <button
                  key={t.key}
                  onClick={() => setTypeFilter(t.key)}
                  className={`rounded-full px-3 py-1.5 text-xs transition-colors ${
                    typeFilter === t.key
                      ? "bg-amber-400 text-zinc-900"
                      : "bg-white/5 text-zinc-400 hover:bg-white/10"
                  }`}
                >
                  {t.label}
                </button>
              ))}
            </div>
          </div>
        </div>

        {/* 记忆列表 */}
        <div className="flex-1 overflow-y-auto px-5 py-3">
          {filtered.length === 0 && (
            <div className="py-10 text-center text-sm text-zinc-600">
              {memories.length === 0 ? "还没有记忆，说句话让我记住你" : "没有匹配的记忆"}
            </div>
          )}
          <div className="space-y-2">
            {filtered.map((m) => {
              const meta = TYPE_META[m.type] || { label: "记忆", icon: "📌" };
              return (
                <div
                  key={m.id}
                  className="group flex items-start gap-3 rounded-xl border border-white/5 bg-white/[0.03] px-4 py-3"
                >
                  <div className="flex min-w-0 flex-1 flex-col">
                    <div className="text-sm leading-relaxed text-zinc-200">{m.content}</div>
                    <div className="mt-1.5 flex items-center gap-2 text-[11px] text-zinc-500">
                      <span>{meta.icon} {meta.label}</span>
                      <span>{"★".repeat(Math.min(m.importance, 10))}</span>
                      <span className="text-zinc-600">{m.updated_at?.slice(0, 16) || ""}</span>
                    </div>
                  </div>
                  <button
                    onClick={() => onDelete(m.id)}
                    className="rounded-lg px-2.5 py-1 text-xs text-zinc-500 transition-colors hover:bg-red-400/10 hover:text-red-300"
                    title="删除这条记忆"
                  >
                    忘记
                  </button>
                </div>
              );
            })}
          </div>
        </div>
      </div>
    </div>
  );
}
