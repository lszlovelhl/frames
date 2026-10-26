// 本地账号认证上下文：token 存 localStorage，数据本地不上云
import { createContext, useContext, useEffect, useState, type ReactNode } from "react";

export const AUTH_KEY = "frames_auth_v1";

interface AuthState {
  token: string | null;
  user_id: string | null;
  username: string | null;
}

interface AuthContextType extends AuthState {
  login: (username: string, password: string) => Promise<string | null>;
  register: (username: string, password: string) => Promise<string | null>;
  logout: () => void;
  authed: boolean;
}

const AuthContext = createContext<AuthContextType | null>(null);

function load(): AuthState {
  try {
    const raw = localStorage.getItem(AUTH_KEY);
    if (raw) return JSON.parse(raw) as AuthState;
  } catch {
    /* ignore */
  }
  return { token: null, user_id: null, username: null };
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AuthState>(load);

  const persist = (s: AuthState) => {
    setState(s);
    try {
      localStorage.setItem(AUTH_KEY, JSON.stringify(s));
    } catch {
      /* ignore */
    }
  };

  async function call(
    path: string,
    body: { username: string; password: string },
  ): Promise<string | null> {
    try {
      const res = await fetch(`/api/auth/${path}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const data = await res.json();
      if (data?.ok && data.token) {
        persist({ token: data.token, user_id: data.user_id, username: data.username });
        return null;
      }
      return data?.message || "操作失败";
    } catch {
      return "网络错误，请确认后端已启动";
    }
  }

  const login = (username: string, password: string) => call("login", { username, password });
  const register = (username: string, password: string) => call("register", { username, password });

  const logout = () => persist({ token: null, user_id: null, username: null });

  // 启动时校验 token 有效性（过期则清除）
  useEffect(() => {
    if (!state.token) return;
    fetch("/api/auth/me", { headers: { Authorization: `Bearer ${state.token}` } })
      .then((r) => r.json())
      .then((d) => {
        if (!d?.ok) persist({ token: null, user_id: null, username: null });
      })
      .catch(() => {
        /* 后端未启动时保持本地状态 */
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <AuthContext.Provider
      value={{ ...state, login, register, logout, authed: !!state.token }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthContextType {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}

/** 给 /api/chat、memories 等请求带认证头和真实 user_id */
export function authHeaders(): Record<string, string> {
  const s = load();
  return s.token ? { Authorization: `Bearer ${s.token}` } : {};
}
