import { useEffect, useState } from "react";
import { api } from "../api";

export default function SettingsView() {
  const [health, setHealth] = useState<string>("");
  const [configured, setConfigured] = useState(false);
  const [models, setModels] = useState<Array<{ provider: string; provider_name: string; id: string; kind: string; label?: string }>>([]);
  const [error, setError] = useState("");

  useEffect(() => {
    void (async () => {
      try {
        const h = await api.health();
        setHealth(h.database);
        const m = await api.models();
        setConfigured(m.configured);
        setModels(m.items ?? []);
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      }
    })();
  }, []);

  const KIND_LABEL: Record<string, string> = {
    flash: "备用（快速问答）",
    pro: "统一拆解档（多模态五层）",
    vision: "画面理解 / 多模态",
    other: "自定义",
  };

  return (
    <div className="mx-auto max-w-3xl px-8 py-8">
      <header className="mb-6">
        <h1 className="text-xl font-semibold text-zinc-100">系统设置</h1>
        <p className="mt-1 text-sm text-zinc-500">后端服务状态与已接入模型网关概览（配置在「模型管理」页维护）</p>
      </header>

      {error && <p className="mb-4 rounded-lg border border-rose-400/20 bg-rose-400/10 px-3 py-2 text-sm text-rose-300">{error}</p>}

      <div className="rounded-xl border border-white/5 bg-[#1c1f26] p-5">
        <div className="flex items-center gap-2">
          <span className={`h-2 w-2 rounded-full ${health === "ok" ? "bg-emerald-400" : "bg-rose-400"}`} />
          <span className="text-sm font-medium text-zinc-200">API 服务</span>
          <span className="ml-auto text-xs text-zinc-500">http://127.0.0.1:8000 · v0.4.0</span>
        </div>
        <div className="mt-2 text-xs text-zinc-400">{health === "ok" ? "数据库连接正常" : "未连接"}</div>
      </div>

      <div className="mt-4 rounded-xl border border-white/5 bg-[#1c1f26] p-5">
        <div className="mb-3 text-sm font-medium text-zinc-200">已接入模型网关</div>
        {!configured && models.length === 0 ? (
          <p className="text-sm text-zinc-500">尚未接入服务商，请到「模型管理」页填写 api-key。</p>
        ) : !configured && models.length > 0 ? (
          <p className="text-sm text-zinc-500">以下为系统预置模板，接入并填写 api-key 后生效：</p>
        ) : (
          <p className="text-xs text-zinc-500">以下服务商已配置 api-key，拆解 / 创作将按优先级自动路由：</p>
        )}
        <table className="mt-2 w-full text-left text-sm">
          <thead>
            <tr className="text-[11px] uppercase tracking-wide text-zinc-500">
              <th className="pb-2">服务商</th>
              <th className="pb-2">模型</th>
              <th className="pb-2">档位</th>
            </tr>
          </thead>
          <tbody>
            {models.map((m) => (
              <tr key={`${m.provider}-${m.id}`} className="border-t border-white/5">
                <td className="py-2 text-zinc-200">{m.provider_name}</td>
                <td className="py-2 font-mono text-zinc-300">{m.id}</td>
                <td className="py-2 text-zinc-500">{KIND_LABEL[m.kind] ?? m.kind}</td>
              </tr>
            ))}
            {models.length === 0 && (
              <tr>
                <td colSpan={3} className="py-2 text-zinc-600">服务未连接</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
