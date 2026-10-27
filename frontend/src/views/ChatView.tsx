import { useEffect, useRef, useState } from "react";
import { useAuth, authHeaders } from "../auth";
import MemoryManager from "../components/MemoryManager";

interface Message {
  role: "user" | "ai";
  text: string;
  time: string;
  streaming?: boolean;
  citations?: { title: string; platform: string; url: string }[];
}

interface MemoryItem {
  id: string;
  type: string;
  content: string;
  importance: number;
  updated_at?: string;
}

interface SessionItem {
  id: string;
  updated_at: string;
  title?: string;
  last_message: string;
}

const MEMORY_ICON: Record<string, string> = {
  preference: "💡",
  feedback: "🔧",
  dream: "💭",
};

function groupSessions(sessions: SessionItem[]): Array<{ label: string; items: SessionItem[] }> {
  const today = new Date().toISOString().slice(0, 10);
  const groups: Array<{ label: string; items: SessionItem[] }> = [];
  const todayItems = sessions.filter((s) => (s.updated_at || "").slice(0, 10) === today);
  const earlier = sessions.filter((s) => (s.updated_at || "").slice(0, 10) !== today);
  if (todayItems.length) groups.push({ label: "今天", items: todayItems });
  if (earlier.length) groups.push({ label: "更早", items: earlier });
  return groups;
}

export default function ChatView() {
  const { user_id } = useAuth();
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [memories, setMemories] = useState<MemoryItem[]>([]);
  const [sessions, setSessions] = useState<SessionItem[]>([]);
  const [currentSession, setCurrentSession] = useState<string | null>(null);
  const [sidebarOpen, setSidebarOpen] = useState(false); // 移动端抽屉
  const [showMemories, setShowMemories] = useState(false); // 记忆管理面板
  const scrollRef = useRef<HTMLDivElement>(null);
  const uid = user_id || "anonymous";

  const loadSessions = async () => {
    try {
      const res = await fetch("/api/chat/sessions", { headers: authHeaders() });
      const data = await res.json();
      setSessions(data.sessions || []);
    } catch {
      setSessions([]);
    }
  };

  const loadMemories = async () => {
    try {
      const res = await fetch("/api/chat/memories", { headers: authHeaders() });
      const data = await res.json();
      setMemories(data.memories || []);
    } catch {
      setMemories([]);
    }
  };

  // 打开页面：先加载会话列表；AI 主动打招呼（仅当用户一条会话都没有时，且不阻塞输入）
  const greetedRef = useRef(false);
  useEffect(() => {
    loadSessions();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 会话列表加载完成后，若完全是新用户，AI 主动打招呼（走流式，不 setLoading）
  useEffect(() => {
    if (sessions.length === 0 && !greetedRef.current) {
      greetedRef.current = true;
      const greeting = "你好";
      setMessages([
        {
          role: "ai",
          text: "你好呀！我是帧间 AI 助手 🤖\n发个视频链接，我帮你拆解；也可以问我问题、让我记住你的偏好。",
          time: new Date().toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" }),
        },
      ]);
      // 让打招呼也成为一个真实会话（走流式端点，逐字出，体验一致）
      (async () => {
        try {
          const resp = await fetch("/api/chat/stream", {
            method: "POST",
            headers: { "Content-Type": "application/json", ...authHeaders() },
            body: JSON.stringify({ message: greeting, user_id: uid, platform: "web" }),
          });
          if (!resp.ok || !resp.body) return;
          const reader = resp.body.getReader();
          const decoder = new TextDecoder();
          let buf = "";
          for (;;) {
            const { done, value } = await reader.read();
            if (done) break;
            buf += decoder.decode(value, { stream: true });
            const events = buf.split("\n\n");
            buf = events.pop() || "";
            for (const ev of events) {
              if (!ev.startsWith("data: ")) continue;
              try {
                const d = JSON.parse(ev.slice(6));
                if (d.type === "delta") {
                  // 只更新初始"打招呼"占位；若用户已发消息/有历史，不覆盖
                  setMessages((prev) => {
                    if (prev.length === 1 && prev[0].role === "ai" && !prev[0].time) {
                      return [{ ...prev[0], text: d.text }];
                    }
                    return prev;
                  });
                } else if (d.type === "done" && d.text) {
                  setMessages((prev) => {
                    if (prev.length === 1 && prev[0].role === "ai" && !prev[0].time) {
                      return [
                        {
                          role: "ai",
                          text: d.text,
                          time: new Date().toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" }),
                        },
                      ];
                    }
                    return prev;
                  });
                }
              } catch {
                // 跳过坏事件
              }
            }
          }
        } catch {
          // 打招呼失败静默，用户可以直接开聊
        }
        loadSessions();
      })();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessions]);

  // 打开某个历史会话：加载历史消息继续聊
  async function openSession(sid: string) {
    setSidebarOpen(false); // 小屏选完后自动收起抽屉
    setCurrentSession(sid);
    setLoading(true);
    try {
      const res = await fetch(`/api/chat/history?session_id=${sid}`, { headers: authHeaders() });
      const data = await res.json();
      const msgs = (data.messages || []).map((m: Message) => ({
        role: m.role,
        text: m.text,
        time: m.time ? String(m.time).slice(11, 16) : "",
      }));
      setMessages(msgs);
    } catch {
      setMessages([]);
    } finally {
      setLoading(false);
    }
  }

  // 新对话
  function newChat() {
    setSidebarOpen(false);
    setCurrentSession(null);
    setMessages([]);
  }

  async function deleteSession(sid: string) {
    try {
      await fetch(`/api/chat/sessions/${sid}`, { method: "DELETE", headers: authHeaders() });
      if (currentSession === sid) {
        setCurrentSession(null);
        setMessages([]);
      }
      loadSessions();
    } catch {
      /* 静默 */
    }
  }

  async function deleteMemory(id: string) {
    try {
      await fetch(`/api/chat/memories/${id}`, { method: "DELETE", headers: authHeaders() });
      setMemories((m) => m.filter((x) => x.id !== id));
    } catch {
      // 删除失败静默，用户可重试
    }
  }

  // 会话分叉（OpenWebUI fork）：从第 idx 条消息后开新分支，原会话保留
  async function forkAt(idx: number) {
    if (!currentSession) return;
    try {
      const resp = await fetch("/api/chat/fork", {
        method: "POST",
        headers: { "Content-Type": "application/json", ...authHeaders() },
        body: JSON.stringify({ session_id: currentSession, up_to_index: idx }),
      });
      const data = await resp.json();
      if (data.session_id) {
        setCurrentSession(data.session_id);
        const msgs = (data.messages || []).map((m: Message) => ({
          role: m.role,
          text: m.text,
          time: new Date().toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" }),
        }));
        setMessages(msgs);
        loadSessions();
      }
    } catch {
      // 分叉失败静默
    }
  }

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, loading]);

  async function send() {
    const text = input.trim();
    if (!text || loading) return;
    setInput("");
    setLoading(true);
    setMessages((m) => [
      ...m,
      { role: "user", text, time: new Date().toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" }) },
    ]);
    // 流式占位：先放一个"思考中"气泡，token 进来后逐字填充
    const aiIdx = messages.length + 1;
    setMessages((m) => [
      ...m,
      { role: "ai", text: "", time: "", streaming: true },
    ]);

    try {
      const resp = await fetch("/api/chat/stream", {
        method: "POST",
        headers: { "Content-Type": "application/json", ...authHeaders() },
        body: JSON.stringify({
          message: text,
          user_id: uid,
          platform: "web",
          session_id: currentSession || undefined,
        }),
      });
      if (!resp.ok || !resp.body) throw new Error("bad status");

      // 解析 SSE：data: {type, text, citations?}
      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buf = "";
      let full = "";
      let citations: Message["citations"] = undefined;
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        const events = buf.split("\n\n");
        buf = events.pop() || "";
        for (const ev of events) {
          if (!ev.startsWith("data: ")) continue;
          try {
            const d = JSON.parse(ev.slice(6));
            if (d.type === "delta") {
              full += d.text;
              setMessages((m) => m.map((x, i) => (i === aiIdx ? { ...x, text: full, time: "" } : x)));
            } else if (d.type === "done") {
              full = d.text || full;
              if (Array.isArray(d.citations) && d.citations.length) {
                citations = d.citations;
              }
            }
          } catch {
            // 跳过坏事件
          }
        }
      }
      if (!full) full = "处理失败了，再试试？";
      setMessages((m) => m.map((x, i) => (i === aiIdx ? { ...x, text: full, time: new Date().toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" }), citations } : x)));
      // 对话后刷新记忆和会话列表（可能自动记了新东西 / 更新了会话）
      loadMemories();
      loadSessions();
    } catch (e) {
      setMessages((m) =>
        m.map((x, i) => (i === aiIdx ? { ...x, text: "网络错误，稍后再试。", time: "" } : x))
      );
    } finally {
      setLoading(false);
    }
  }

  const groups = groupSessions(sessions);

  return (
    <div className="flex h-full">
      {/* 小屏抽屉遮罩 */}
      {sidebarOpen && (
        <div
          className="fixed inset-0 z-30 bg-[var(--bg-base)] md:hidden"
          onClick={() => setSidebarOpen(false)}
        />
      )}

      {/* 会话历史侧栏（OpenWebUI 式：自动保存、可继续、按时间分组）
          移动端：fixed 抽屉（默认隐藏，点 ☰ 打开）；≥768px：常驻左侧 */}
      <aside
        className={`fixed inset-y-0 left-0 z-40 flex w-64 shrink-0 flex-col border-r border-[var(--separator-soft)] bg-[var(--bg-base)] transition-transform duration-200 md:static md:w-56 md:translate-x-0 md:transition-none ${
          sidebarOpen ? "translate-x-0" : "-translate-x-full"
        }`}
      >
        <div className="border-b border-[var(--separator-soft)] px-3 py-2.5">
          <button
            onClick={newChat}
            className={`w-full rounded-full px-3 py-2 text-left text-[13px] font-medium transition-colors ${
              !currentSession
                ? "bg-[var(--accent)] text-white"
                : "bg-[var(--bg-card-hi)] text-[var(--text-1)] hover:bg-[var(--bg-hover)]"
            }`}
          >
            ✚ 新对话
          </button>
        </div>
        <div className="flex-1 overflow-y-auto px-2 py-2">
          {groups.length === 0 && (
            <div className="px-3 py-6 text-center text-xs text-[var(--text-3)]">
              还没有历史会话
              <br />
              说句话开始吧
            </div>
          )}
          {groups.map((g) => (
            <div key={g.label} className="mb-3">
              <div className="mb-1 px-2 text-[10px] uppercase tracking-wider text-[var(--text-3)]">{g.label}</div>
              {g.items.map((s) => (
                <div
                  key={s.id}
                  className={`group mb-0.5 flex cursor-pointer items-center gap-1 rounded-[10px] px-2 py-1.5 text-left transition-colors ${
                    currentSession === s.id
                      ? "bg-[var(--bg-selected)] text-[var(--accent)]"
                      : "text-[var(--text-2)] hover:bg-[var(--bg-hover)] hover:text-[var(--text-1)]"
                  }`}
                  onClick={() => openSession(s.id)}
                >
                  <span className="flex-1 truncate text-xs">{s.title || s.last_message || "空会话"}</span>
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      deleteSession(s.id);
                    }}
                    className="hidden rounded px-1 text-[var(--text-3)] hover:text-[var(--danger)] group-hover:block"
                    title="删除会话"
                  >
                    ×
                  </button>
                </div>
              ))}
            </div>
          ))}
        </div>
      </aside>

      {/* 主对话区 */}
      <div className="flex min-w-0 flex-1 flex-col">
        <div className="liquid-glass flex items-center gap-2 px-4 py-3 md:px-5">
          {/* 移动端：打开会话抽屉 */}
          <button
            onClick={() => setSidebarOpen(true)}
            className="btn btn-ghost btn-icon md:hidden"
            title="历史会话"
          >
            ☰
          </button>
          <div className="min-w-0">
            <h1 className="text-[15px] font-semibold text-[var(--text-1)]">AI 助手</h1>
            <p className="mt-0.5 text-xs text-[var(--text-3)]">说人话就能用，发链接自动拆解</p>
          </div>
        </div>

        {/* AI 记得你：记忆透明展示，可删除 */}
        {memories.length > 0 && (
          <div className="border-b border-[var(--separator-soft)] px-4 py-3 md:px-5">
            <div className="mb-1.5 flex items-center justify-between">
              <span className="text-xs text-[var(--text-2)]">🧠 AI 记得你</span>
              <button
                onClick={() => setShowMemories(true)}
                className="text-[10px] text-[var(--text-3)] transition-colors hover:text-[var(--accent)]"
              >
                管理全部 →
              </button>
            </div>
            <div className="flex flex-wrap gap-1.5">
              {memories.slice(0, 8).map((mem) => (
                <span
                  key={mem.id}
                  title={`${mem.type === "preference" ? "偏好" : mem.type === "feedback" ? "反馈" : "结晶"} · 重要度 ${mem.importance}`}
                  className="group inline-flex items-center gap-1 rounded-full border border-[var(--separator-soft)] bg-[var(--bg-card)] py-1 pl-2.5 pr-1 text-xs text-[var(--text-2)]"
                >
                  {MEMORY_ICON[mem.type] || "📌"} {mem.content.length > 18 ? mem.content.slice(0, 18) + "…" : mem.content}
                  <button
                    onClick={() => deleteMemory(mem.id)}
                    className="rounded-full px-1 text-[var(--text-3)] transition-colors hover:bg-[rgba(255,69,58,0.15)] hover:text-[var(--danger)]"
                    title="删除这条记忆"
                  >
                    ×
                  </button>
                </span>
              ))}
            </div>
          </div>
        )}

        <div ref={scrollRef} data-scroll="main" className="flex-1 space-y-4 overflow-y-auto px-4 py-4 md:px-5">
          {messages.map((m, i) => (
            <div key={i} className={`flex ${m.role === "user" ? "justify-end" : "justify-start"}`}>
              <div
                className={`group max-w-[88%] rounded-[18px] px-4 py-2.5 text-sm leading-relaxed md:max-w-[80%] ${
                  m.role === "user"
                    ? "rounded-br-[6px] bg-[var(--accent)] text-white shadow-[0_2px_10px_rgba(10,132,255,0.3)]"
                    : "rounded-bl-[6px] border border-[var(--separator-soft)] bg-[var(--bg-card)] text-[var(--text-1)]"
                }`}
              >
                <div className="whitespace-pre-wrap">
                  {m.text || (m.streaming ? <span className="text-[var(--text-3)]">正在思考<span className="animate-pulse">…</span></span> : "")}
                  {m.streaming && m.text && <span className="ml-0.5 inline-block h-3.5 w-[2px] animate-pulse bg-[var(--accent)] align-middle" />}
                </div>
                {m.citations && m.citations.length > 0 && (
                  <div className="mt-1.5 border-t border-[var(--separator-soft)] pt-1.5">
                    <div className="mb-0.5 text-[10px] text-[var(--text-3)]">参考来源</div>
                    <div className="flex flex-wrap gap-1">
                      {m.citations.map((c, ci) => (
                        <a
                          key={ci}
                          href={c.url || "#"}
                          target="_blank"
                          rel="noopener noreferrer"
                          onClick={(e) => !c.url && e.preventDefault()}
                          className="max-w-full truncate rounded bg-[var(--bg-hover)] px-1.5 py-0.5 text-[10px] text-[var(--accent-hover)] hover:bg-[var(--bg-active)]"
                          title={c.title}
                        >
                          {c.platform ? `[${c.platform}] ` : ""}{c.title}
                        </a>
                      ))}
                    </div>
                  </div>
                )}
                <div className="mt-1 flex items-center justify-end gap-2">
                  <span className={`text-[10px] ${m.role === "user" ? "text-white/60" : "text-[var(--text-3)]"}`}>
                    {m.time}
                  </span>
                  {m.role === "ai" && currentSession && (
                    <button
                      onClick={() => forkAt(i)}
                      className="hidden rounded px-1 text-[10px] text-[var(--text-3)] transition-colors hover:text-[var(--accent)] group-hover:block md:block"
                      title="从这里分叉出新对话，尝试不同方向"
                    >
                      ⑂ 分叉
                    </button>
                  )}
                </div>
              </div>
            </div>
          ))}
          {loading && !messages.some((x) => x.streaming) && (
            <div className="flex justify-start">
              <div className="rounded-[18px] rounded-bl-[6px] border border-[var(--separator-soft)] bg-[var(--bg-card)] px-4 py-2.5 text-sm text-[var(--text-3)]">
                思考中...
              </div>
            </div>
          )}
        </div>

        <div className="border-t border-[var(--separator-soft)] px-4 py-3 md:px-5">
          <div className="mb-2 flex flex-wrap gap-1.5">
            {["帮我拆解这个视频 https://v.douyin.com/xxx", "拆完了吗", "还有类似的吗", "记住我喜欢口播类"].map((q) => (
              <button
                key={q}
                onClick={() => setInput(q)}
                className="rounded-full border border-[var(--separator-soft)] bg-[var(--bg-card)] px-3 py-1 text-xs text-[var(--text-2)] transition-colors hover:border-[var(--accent)] hover:text-[var(--accent)]"
              >
                {q.length > 12 ? q.slice(0, 12) + "..." : q}
              </button>
            ))}
          </div>
          <div className="flex items-center gap-2">
            <input
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && send()}
              placeholder="发视频链接，或问我问题..."
              className="input min-w-0 flex-1 !rounded-full !py-2.5"
            />
            <button
              onClick={send}
              disabled={loading || !input.trim()}
              className="btn btn-primary !rounded-full !px-5 !py-2.5 disabled:opacity-50"
            >
              发送
            </button>
          </div>
        </div>
      </div>

      {/* 记忆管理面板：完整查看/搜索/筛选/删除（透明控制） */}
      {showMemories && (
        <MemoryManager
          memories={memories}
          onDelete={deleteMemory}
          onClose={() => setShowMemories(false)}
        />
      )}
    </div>
  );
}
