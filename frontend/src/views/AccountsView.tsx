import { useCallback, useEffect, useState } from "react";
import { api, type PlatformStatus } from "../api";

const STATUS_META: Record<PlatformStatus["status"], { label: string; cls: string }> = {
  none: { label: "未配置", cls: "bg-zinc-500/15 text-zinc-400" },
  imported: { label: "已导入待验证", cls: "bg-sky-400/10 text-sky-300" },
  valid: { label: "登录有效", cls: "bg-emerald-400/10 text-emerald-300" },
  expired: { label: "已过期 · 请重新登录", cls: "bg-rose-400/10 text-rose-300" },
};

function fmt(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")} ${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

export default function AccountsView() {
  const [items, setItems] = useState<PlatformStatus[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");
  const [editing, setEditing] = useState<PlatformStatus | null>(null);
  const [cookiesText, setCookiesText] = useState("");
  
  // 扫码登录状态
  const [qrLogin, setQrLogin] = useState<{
    platform: PlatformStatus;
    sessionId: string;
    qrImage: string;
    status: string;
  } | null>(null);
  const [verifyCode, setVerifyCode] = useState("");

  const load = useCallback(async () => {
    try {
      setError("");
      setItems(await api.listPlatforms());
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function run<T>(key: string, fn: () => Promise<T>, after?: () => Promise<void>): Promise<void> {
    setBusy(key);
    try {
      await fn();
      if (after) await after();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy("");
    }
  }

  function openImport(p: PlatformStatus) {
    setEditing(p);
    setCookiesText("");
    setError("");
  }

  async function saveCookies() {
    if (!editing) return;
    await run(`save:${editing.platform}`, () => api.savePlatformCookies(editing.platform, cookiesText));
    setEditing(null);
    await load();
  }

  async function openLogin(p: PlatformStatus) {
    try {
      const u = await api.platformLoginUrl(p.platform);
      window.open(u.login_url, "_blank");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  // 扫码登录
  async function startQrLogin(p: PlatformStatus) {
    setError("");
    try {
      const result = await api.startQrLogin(p.platform);
      setQrLogin({
        platform: p,
        sessionId: result.session_id,
        qrImage: result.qr_image,
        status: result.status,
      });
      // 启动轮询
      pollQrLogin(result.session_id);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  async function pollQrLogin(sessionId: string) {
    const interval = setInterval(async () => {
      try {
        const status = await api.getQrLoginStatus(sessionId);
        setQrLogin((prev) => {
          if (!prev || prev.sessionId !== sessionId) {
            clearInterval(interval);
            return prev;
          }
          return {
            ...prev,
            status: status.status,
            qrImage: status.qr_image || prev.qrImage,
          };
        });
        
        // 登录成功或过期，停止轮询
        if (status.status === "success" || status.status === "expired") {
          clearInterval(interval);
          if (status.status === "success") {
            // 登录成功，刷新列表
            await load();
            // 2秒后关闭弹窗
            setTimeout(() => setQrLogin(null), 2000);
          }
        }
      } catch (e) {
        console.error("轮询扫码状态失败", e);
      }
    }, 3000);
  }

  return (
    <div className="mx-auto max-w-4xl px-8 py-8">
      <header className="mb-6">
        <h1 className="text-xl font-semibold text-zinc-100">采集账号</h1>
        <p className="mt-1 text-sm text-zinc-500">平台登录态集中管理 · 抓取时自动注入 cookie 规避反爬，失效自动提醒</p>
      </header>

      {error && <p className="mb-4 rounded-lg border border-rose-400/20 bg-rose-400/10 px-3 py-2 text-sm text-rose-300">{error}</p>}

      <div className="mb-6 rounded-xl border border-white/5 bg-[#1c1f26] p-4 text-[13px] leading-relaxed text-zinc-400">
        <div className="mb-1 font-medium text-zinc-200">登录态如何工作</div>
        需登录平台（抖音、小红书、微信视频号）首次接入：先在浏览器登录该平台 → 用
        <span className="mx-1 font-mono text-amber-200/90">EditThisCookie / Cookie-Editor</span>
        扩展导出为 Netscape cookies.txt 后粘贴导入。抓取成功自动标记「登录有效」，被平台判定需登录/风控自动标记「已过期」并提示重新导入；超过 90 天未成功使用也会自动过期。小红书与视频号目前无公开稳定取流，登录态仅作反爬准备。
      </div>

      <div className="flex flex-col gap-3">
        {items.map((p) => {
          const meta = STATUS_META[p.status] ?? STATUS_META.none;
          return (
            <div key={p.platform} className="rounded-xl border border-white/5 bg-[#1c1f26] p-4">
              <div className="flex items-start gap-3">
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-medium text-zinc-100">{p.label}</span>
                    <span className="font-mono text-[11px] text-zinc-600">{p.platform}</span>
                    {!p.need_login && (
                      <span className="rounded bg-white/5 px-1.5 py-0.5 text-[11px] text-zinc-500">免登录</span>
                    )}
                    {!p.downloadable && (
                      <span className="rounded bg-white/5 px-1.5 py-0.5 text-[11px] text-zinc-500">暂不可自动取流</span>
                    )}
                    <span className={`rounded px-2 py-0.5 text-[11px] font-medium ${meta.cls}`}>{meta.label}</span>
                  </div>
                  {p.note && <div className="mt-1 text-xs text-zinc-500">{p.note}</div>}
                  <div className="mt-1.5 text-[11px] text-zinc-600">
                    登录态更新 {fmt(p.updated_at)}
                    {p.last_success_at && <> · 最近成功 {fmt(p.last_success_at)}</>}
                    {p.last_fail_at && <> · 最近失败 {fmt(p.last_fail_at)}</>}
                  </div>
                </div>
                <div className="flex shrink-0 flex-col items-end gap-1.5">
                  <div className="flex flex-wrap justify-end gap-1.5">
                    <button
                      onClick={() => openImport(p)}
                      className="rounded-lg bg-amber-300/15 px-2.5 py-1.5 text-xs font-medium text-amber-200 hover:bg-amber-300/25 disabled:opacity-50"
                      disabled={busy === `save:${p.platform}`}
                    >
                      {p.has_cookie ? "更新登录态" : "导入登录态"}
                    </button>
                    <button
                      onClick={() => startQrLogin(p)}
                      className="rounded-lg bg-emerald-400/15 px-2.5 py-1.5 text-xs font-medium text-emerald-300 hover:bg-emerald-400/25 disabled:opacity-50"
                      disabled={busy === `qr:${p.platform}`}
                    >
                      📱 扫码登录
                    </button>
                    <button
                      onClick={() => openLogin(p)}
                      className="rounded-lg bg-white/5 px-2.5 py-1.5 text-xs text-zinc-300 hover:bg-white/10"
                    >
                      打开登录页
                    </button>
                    <button
                      onClick={() => run(`detect:${p.platform}`, () => api.detectPlatform(p.platform), load)}
                      className="rounded-lg bg-white/5 px-2.5 py-1.5 text-xs text-zinc-300 hover:bg-white/10 disabled:opacity-50"
                      disabled={busy === `detect:${p.platform}` || !p.has_cookie}
                    >
                      立即检测
                    </button>
                    {p.has_cookie && (
                      <button
                        onClick={() => run(`clear:${p.platform}`, () => api.clearPlatformCookies(p.platform), load)}
                        className="rounded-lg bg-white/5 px-2.5 py-1.5 text-xs text-zinc-500 hover:bg-rose-400/10 hover:text-rose-300 disabled:opacity-50"
                        disabled={busy === `clear:${p.platform}`}
                      >
                        清除
                      </button>
                    )}
                  </div>
                  {busy === `detect:${p.platform}` && <span className="text-[11px] text-zinc-600">检测中…</span>}
                </div>
              </div>
            </div>
          );
        })}
      </div>

      {/* 导入弹窗 */}
      {editing && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-6" onClick={() => setEditing(null)}>
          <div className="w-full max-w-xl rounded-xl border border-white/10 bg-[#171a20] p-5" onClick={(e) => e.stopPropagation()}>
            <div className="mb-1 flex items-center justify-between">
              <h3 className="text-base font-medium text-zinc-100">导入 {editing.label} 登录态</h3>
              <button onClick={() => setEditing(null)} className="text-zinc-500 hover:text-zinc-300">✕</button>
            </div>
            <p className="mb-3 text-xs leading-relaxed text-zinc-500">
              在 Chrome 中安装 <span className="font-mono text-amber-200/90">Cookie-Editor</span>，登录{editing.label}后打开扩展 →
              Export → 选择 <span className="font-mono text-amber-200/90">Header String / Netscape</span> 格式导出，把内容整段粘贴到下方。
            </p>
            <textarea
              value={cookiesText}
              onChange={(e) => setCookiesText(e.target.value)}
              rows={10}
              spellCheck={false}
              placeholder={"# Netscape HTTP Cookie File\n.douyin.com\tTRUE\t/\tTRUE\t...\tsessionid\txxx"}
              className="w-full resize-y rounded-lg border border-white/10 bg-black/30 p-3 font-mono text-[11px] leading-relaxed text-zinc-300 outline-none placeholder:text-zinc-700 focus:border-amber-300/40"
            />
            <div className="mt-3 flex justify-end gap-2">
              <button onClick={() => setEditing(null)} className="rounded-lg bg-white/5 px-3 py-1.5 text-xs text-zinc-400 hover:bg-white/10">
                取消
              </button>
              <button
                onClick={saveCookies}
                disabled={!cookiesText.trim() || busy === `save:${editing.platform}`}
                className="rounded-lg bg-amber-300 px-3 py-1.5 text-xs font-semibold text-black hover:bg-amber-200 disabled:opacity-50"
              >
                保存登录态
              </button>
            </div>
          </div>
        </div>
      )}

      {/* 扫码登录弹窗 */}
      {qrLogin && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-6" onClick={() => setQrLogin(null)}>
          <div className="w-full max-w-sm rounded-xl border border-white/10 bg-[#171a20] p-5" onClick={(e) => e.stopPropagation()}>
            <div className="mb-1 flex items-center justify-between">
              <h3 className="text-base font-medium text-zinc-100">扫码登录 {qrLogin.platform.label}</h3>
              <button onClick={() => setQrLogin(null)} className="text-zinc-500 hover:text-zinc-300">✕</button>
            </div>
            <p className="mb-4 text-xs text-zinc-500">
              打开{qrLogin.platform.label}手机App → 扫一扫，即可自动登录
            </p>
            
            <div className="flex justify-center py-4">
              {(qrLogin.status === "waiting" || qrLogin.status === "waiting_verify") && (
                <div className="text-center">
                  <img src={qrLogin.qrImage} alt="登录二维码" className="mx-auto h-48 w-48 rounded-lg border border-white/10 opacity-80" />
                  {qrLogin.status === "waiting" ? (
                    <p className="mt-3 text-xs text-zinc-500">请使用手机扫码登录（二维码自动刷新）</p>
                  ) : (
                    <>
                      <p className="mt-3 text-xs text-amber-300">📱 请在手机上完成验证，或输入收到的短信验证码</p>
                      <div className="mt-3 flex gap-2 justify-center">
                        <input
                          type="text"
                          value={verifyCode}
                          onChange={(e) => setVerifyCode(e.target.value)}
                          placeholder="输入6位验证码"
                          className="w-32 rounded-lg border border-white/10 bg-black/30 px-3 py-1.5 text-sm text-zinc-200 outline-none focus:border-amber-300/40"
                          maxLength={6}
                        />
                        <button
                          onClick={async () => {
                            if (!verifyCode.trim() || !qrLogin) return;
                            try {
                              await fetch(`/api/collection/qr-login/submit-code/${qrLogin.sessionId}`, {
                                method: "POST",
                                headers: { "Content-Type": "application/json" },
                                body: JSON.stringify({ code: verifyCode.trim() }),
                              });
                              setVerifyCode("");
                            } catch (e) {
                              console.error("提交验证码失败", e);
                            }
                          }}
                          className="rounded-lg bg-amber-300 px-3 py-1.5 text-xs font-medium text-black hover:bg-amber-200"
                        >
                          提交
                        </button>
                      </div>
                    </>
                  )}
                </div>
              )}
              {qrLogin.status === "success" && (
                <div className="py-8 text-center">
                  <div className="mb-2 text-4xl">✅</div>
                  <p className="text-sm text-emerald-300">登录成功！</p>
                </div>
              )}
              {qrLogin.status === "expired" && (
                <div className="py-8 text-center">
                  <div className="mb-2 text-4xl">⏰</div>
                  <p className="text-sm text-rose-300">二维码已过期，请关闭后重新扫码</p>
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
