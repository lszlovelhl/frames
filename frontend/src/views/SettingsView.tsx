import { useEffect, useState } from "react";
import { api } from "../api";

export default function SettingsView() {
  const [health, setHealth] = useState<string>("");
  const [models, setModels] = useState<Array<{ alias: string; model: string }>>([]);
  const [error, setError] = useState("");

  useEffect(() => {
    void (async () => {
      try {
        const [h, m] = await Promise.all([api.health(), api.models()]);
        setHealth(h.database);
        setModels(m);
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      }
    })();
  }, []);

  return (
    <div className="mx-auto max-w-3xl px-8 py-8">
      <header className="mb-6">
        <h1 className="text-xl font-semibold text-zinc-100">系统设置</h1>
        <p className="mt-1 text-sm text-zinc-500">帧间后端服务与模型网关状态（读取 .env 配置，不做写入）</p>
      </header>

      {error && <p className="mb-4 rounded-lg border border-rose-400/20 bg-rose-400/10 px-3 py-2 text-sm text-rose-300">{error}</p>}

      <div className="rounded-xl border border-white/5 bg-[#1c1f26] p-5">
        <div className="flex items-center gap-2">
          <span className={`h-2 w-2 rounded-full ${health === "ok" ? "bg-emerald-400" : "bg-rose-400"}`} />
          <span className="text-sm font-medium text-zinc-200">API 服务</span>
          <span className="ml-auto text-xs text-zinc-500">http://127.0.0.1:8000 · v0.3.0</span>
        </div>
        <div className="mt-2 text-xs text-zinc-400">{health === "ok" ? "数据库连接正常" : "未连接"}</div>
      </div>

      <div className="mt-4 rounded-xl border border-white/5 bg-[#1c1f26] p-5">
        <div className="mb-3 text-sm font-medium text-zinc-200">模型网关（DeepSeek）</div>
        <table className="w-full text-left text-sm">
          <thead>
            <tr className="text-[11px] uppercase tracking-wide text-zinc-500">
              <th className="pb-2">别名</th>
              <th className="pb-2">实际模型</th>
              <th className="pb-2">用途</th>
            </tr>
          </thead>
          <tbody>
            {models.map((m) => (
              <tr key={m.alias} className="border-t border-white/5">
                <td className="py-2 font-mono text-amber-200/90">{m.alias}</td>
                <td className="py-2 font-mono text-zinc-300">{m.model}</td>
                <td className="py-2 text-zinc-500">{m.alias === "flash" ? "备用（快速问答）" : m.alias === "pro" ? "统一拆解档（多模态五层）" : "画面理解 / 多模态（已接入）"}</td>
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
