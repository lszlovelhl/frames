import { useEffect, useState } from "react";
import { api } from "../api";
import LiquidSlider from "../components/LiquidSlider";

// 界面质感（液态玻璃强度）：滑块值 → CSS 变量
const GLASS_KEY = "frames-glass-strength";
function glassFromStorage(): number {
  const v = Number(localStorage.getItem(GLASS_KEY));
  return Number.isFinite(v) ? v : 60; // 默认 60
}
function applyGlass(strength: number) {
  const blur = Math.round(8 + (strength / 100) * 32);       // 8~40px
  const alpha = 0.04 + (strength / 100) * 0.14;              // 0.04~0.18
  const root = document.documentElement;
  root.style.setProperty("--glass-blur", `${blur}px`);
  root.style.setProperty("--glass-alpha", alpha.toFixed(3));
  root.style.setProperty("--glass-saturate", `${Math.round(150 + strength * 0.8)}%`);
}

export default function SettingsView() {
  const [health, setHealth] = useState<string>("");
  const [configured, setConfigured] = useState(false);
  const [models, setModels] = useState<Array<{ provider: string; provider_name: string; id: string; kind: string; label?: string }>>([]);
  const [error, setError] = useState("");
  const [glass, setGlass] = useState<number>(glassFromStorage);

  useEffect(() => {
    applyGlass(glass);
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
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function onGlassChange(v: number) {
    setGlass(v);
    localStorage.setItem(GLASS_KEY, String(v));
    applyGlass(v);
  }

  const KIND_LABEL: Record<string, string> = {
    flash: "备用（快速问答）",
    pro: "统一拆解档（多模态五层）",
    vision: "画面理解 / 多模态",
    other: "自定义",
  };

  return (
    <div className="mx-auto max-w-3xl px-8 py-8">
      <header className="mb-6">
        <h1 className="text-xl font-semibold text-[var(--text-1)]">系统设置</h1>
        <p className="mt-1 text-sm text-[var(--text-3)]">后端服务状态与已接入模型网关概览（配置在「模型管理」页维护）</p>
      </header>

      {error && <p className="mb-4 rounded-lg border border-rose-400/20 bg-rose-400/10 px-3 py-2 text-sm text-rose-300">{error}</p>}

      {/* 界面质感：液态玻璃强度（全局实时生效，本地记忆） */}
      <div className="mb-4 rounded-xl border border-[var(--separator-soft)] bg-[var(--bg-card)] p-5">
        <div className="mb-1 flex items-center gap-2">
          <span className="text-sm font-medium text-[var(--text-1)]">界面质感</span>
          <span className="ml-auto text-xs text-[var(--text-3)]">液态玻璃强度 · 实时预览</span>
        </div>
        <p className="mb-3 text-xs text-[var(--text-3)]">调节底部悬浮栏与顶栏的毛玻璃模糊与折射高光，偏好保存在本机</p>
        <div className="flex items-center gap-3">
          <span className="text-[11px] text-[var(--text-3)]">轻透</span>
          <div className="flex-1">
            <LiquidSlider value={glass} min={20} max={100} onChange={onGlassChange} ariaLabel="液态玻璃强度" />
          </div>
          <span className="text-[11px] text-[var(--text-3)]">浓重</span>
          <span className="w-8 text-right text-xs tabular-nums text-[var(--accent)]">{glass}</span>
        </div>
      </div>

      <div className="rounded-xl border border-[var(--separator-soft)] bg-[var(--bg-card)] p-5">
        <div className="flex items-center gap-2">
          <span className={`h-2 w-2 rounded-full ${health === "ok" ? "bg-emerald-400" : "bg-rose-400"}`} />
          <span className="text-sm font-medium text-[var(--text-1)]">API 服务</span>
          <span className="ml-auto text-xs text-[var(--text-3)]">http://127.0.0.1:8000 · v0.4.0</span>
        </div>
        <div className="mt-2 text-xs text-[var(--text-2)]">{health === "ok" ? "数据库连接正常" : "未连接"}</div>
      </div>

      <div className="mt-4 rounded-xl border border-[var(--separator-soft)] bg-[var(--bg-card)] p-5">
        <div className="mb-3 text-sm font-medium text-[var(--text-1)]">已接入模型网关</div>
        {!configured && models.length === 0 ? (
          <p className="text-sm text-[var(--text-1)]0">尚未接入服务商，请到「模型管理」页填写 api-key。</p>
        ) : !configured && models.length > 0 ? (
          <p className="text-sm text-[var(--text-1)]0">以下为系统预置模板，接入并填写 api-key 后生效：</p>
        ) : (
          <p className="text-xs text-[var(--text-1)]0">以下服务商已配置 api-key，拆解 / 创作将按优先级自动路由：</p>
        )}
        <table className="mt-2 w-full text-left text-sm">
          <thead>
            <tr className="text-[11px] uppercase tracking-wide text-[var(--text-1)]0">
              <th className="pb-2">服务商</th>
              <th className="pb-2">模型</th>
              <th className="pb-2">档位</th>
            </tr>
          </thead>
          <tbody>
            {models.map((m) => (
              <tr key={`${m.provider}-${m.id}`} className="border-t border-[var(--separator-soft)]">
                <td className="py-2 text-[var(--text-1)]">{m.provider_name}</td>
                <td className="py-2 font-mono text-[var(--text-2)]">{m.id}</td>
                <td className="py-2 text-[var(--text-1)]0">{KIND_LABEL[m.kind] ?? m.kind}</td>
              </tr>
            ))}
            {models.length === 0 && (
              <tr>
                <td colSpan={3} className="py-2 text-[var(--text-3)]">服务未连接</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
