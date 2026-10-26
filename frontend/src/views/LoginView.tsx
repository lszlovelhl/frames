// 登录 / 注册页（本地账号，数据不上云）—— 苹果风
import { useState } from "react";
import { useAuth } from "../auth";
import { IconSparkles } from "../components/Icons";

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
    <div className="flex h-screen items-center justify-center bg-[var(--bg-elevated)] px-4">
      <div className="anim-pop w-full max-w-sm">
        <div className="card px-8 py-9">
          <div className="mb-7 flex flex-col items-center gap-3 text-center">
            <div className="relative">
              <img src="/logo.png" alt="帧间" className="h-14 w-14 rounded-[16px] shadow-[0_8px_24px_rgba(10,132,255,0.35)]" />
              <span className="absolute -bottom-1 -right-1 flex h-5 w-5 items-center justify-center rounded-full bg-[var(--accent)] text-[11px] text-white shadow-[0_2px_8px_rgba(10,132,255,0.5)]">
                <IconSparkles />
              </span>
            </div>
            <div>
              <div className="text-[22px] font-semibold tracking-tight text-white">帧间</div>
              <div className="mt-1 text-[12.5px] text-[var(--text-2)]">AI 短视频拆解伙伴</div>
            </div>
          </div>

          <div className="mb-5 flex rounded-full bg-[var(--bg-card-hi)] p-1">
            {(["login", "register"] as const).map((m) => (
              <button
                key={m}
                onClick={() => { setMode(m); setError(null); }}
                className={`flex-1 rounded-full py-1.5 text-[13px] transition-all duration-150 ${
                  mode === m
                    ? "bg-[var(--accent)] font-medium text-white shadow-[0_2px_10px_rgba(10,132,255,0.4)]"
                    : "text-[var(--text-2)] hover:text-[var(--text-1)]"
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
              className="input w-full"
            />
            <input
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && submit()}
              type="password"
              placeholder="密码"
              autoComplete={mode === "login" ? "current-password" : "new-password"}
              className="input w-full"
            />
            {error && (
              <div className="anim-fade-in rounded-[12px] bg-[rgba(255,69,58,0.1)] px-3.5 py-2.5 text-[12px] text-[var(--danger)]">
                {error}
              </div>
            )}
            <button
              onClick={submit}
              disabled={busy}
              className="btn btn-primary w-full !py-2.5 text-[14px] disabled:opacity-50"
            >
              {busy ? "请稍候…" : mode === "login" ? "进入帧间" : "创建账号"}
            </button>
          </div>

          <p className="mt-6 text-center text-[11px] leading-relaxed text-[var(--text-3)]">
            本地账号 · 记忆只存你的电脑，不上云
            <br />
            密码加密存储，只有你能登录
          </p>
        </div>
      </div>
    </div>
  );
}
