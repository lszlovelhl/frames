// 登录 / 注册页（本地账号，数据不上云）
import { useState } from "react";
import { useAuth } from "../auth";

export default function LoginView() {
  const { login, register } = useAuth();
  const [mode, setMode] = useState<"login" | "register">("login");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit() {
    if (!username.trim() || !password) {
      setError(mode === "login" ? "请输入用户名和密码" : "用户名至少 2 个字符，密码至少 6 位");
      return;
    }
    setBusy(true);
    setError(null);
    const err = mode === "login"
      ? await login(username, password)
      : await register(username, password);
    setBusy(false);
    if (err) setError(err);
  }

  return (
    <div className="flex h-screen items-center justify-center bg-[#1a1d23] px-4">
      <div className="w-full max-w-sm rounded-2xl border border-white/10 bg-[#1e2229] p-8">
        <div className="mb-6 flex items-center gap-3">
          <img src="/logo.png" alt="帧间" className="h-10 w-10 rounded-xl" />
          <div>
            <div className="text-xl font-semibold tracking-wide text-amber-300">帧间</div>
            <div className="mt-0.5 text-xs text-zinc-500">AI 短视频拆解伙伴</div>
          </div>
        </div>

        <div className="mb-4 flex rounded-lg bg-white/5 p-1 text-sm">
          {(["login", "register"] as const).map((m) => (
            <button
              key={m}
              onClick={() => { setMode(m); setError(null); }}
              className={`flex-1 rounded-md py-1.5 transition-colors ${
                mode === m ? "bg-amber-400 font-medium text-zinc-900" : "text-zinc-400 hover:text-zinc-200"
              }`}
            >
              {m === "login" ? "登录" : "注册"}
            </button>
          ))}
        </div>

        <div className="space-y-3">
          <input
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            placeholder="用户名"
            autoComplete="username"
            className="w-full rounded-lg border border-white/10 bg-[#252932] px-4 py-2.5 text-sm text-zinc-200 placeholder-zinc-500 outline-none focus:border-amber-400/50"
          />
          <input
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && submit()}
            type="password"
            placeholder="密码"
            autoComplete={mode === "login" ? "current-password" : "new-password"}
            className="w-full rounded-lg border border-white/10 bg-[#252932] px-4 py-2.5 text-sm text-zinc-200 placeholder-zinc-500 outline-none focus:border-amber-400/50"
          />
          {error && <div className="rounded-lg bg-red-500/10 px-3 py-2 text-xs text-red-300">{error}</div>}
          <button
            onClick={submit}
            disabled={busy}
            className="w-full rounded-lg bg-amber-400 py-2.5 text-sm font-medium text-zinc-900 transition-colors hover:bg-amber-300 disabled:opacity-50"
          >
            {busy ? "请稍候…" : mode === "login" ? "进入帧间" : "创建账号"}
          </button>
        </div>

        <p className="mt-5 text-center text-[11px] leading-relaxed text-zinc-600">
          本地账号 · 记忆只存你的电脑，不上云
          <br />
          密码加密存储，只有你能登录
        </p>
      </div>
    </div>
  );
}
