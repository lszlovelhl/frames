import { useEffect, useRef, useState } from "react";
import { useAuth } from "../auth";

interface Message {
  role: "user" | "ai";
  text: string;
  time: string;
}

interface MemoryItem {
  id: string;
  type: string;
  content: string;
  importance: number;
}

const MEMORY_ICON: Record<string, string> = {
  preference: "💡",
  feedback: "🔧",
  dream: "💭",
};

export default function ChatView() {
  const { user_id } = useAuth();
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [memories, setMemories] = useState<MemoryItem[]>([]);
  const scrollRef = useRef<HTMLDivElement>(null);
  const uid = user_id || "anonymous";

  // 打开页面时自动打个招呼，AI 根据状态主动说话
  useEffect(() => {
    async function greet() {
      setLoading(true);
      try {
        const res = await fetch("/api/chat", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ message: "你好", user_id: uid, platform: "web" }),
        });
        const data = await res.json();
        setMessages([
          {
            role: "ai",
            text: data.reply,
            time: new Date().toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" }),
          },
        ]);
      } catch {
        setMessages([
          {
            role: "ai",
            text: "我是帧间 AI 助手 🤖\n\n发个视频链接，我帮你拆解分析。",
            time: new Date().toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" }),
          },
        ]);
      } finally {
        setLoading(false);
      }
    }
    greet();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 加载 AI 记住的事（OpenWebUI 式透明记忆：可见、可删）
  useEffect(() => {
    async function loadMemories() {
      try {
        const res = await fetch(`/api/chat/memories?user_id=${uid}`);
        const data = await res.json();
        setMemories(data.memories || []);
      } catch {
        setMemories([]);
      }
    }
    loadMemories();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function deleteMemory(id: string) {
    try {
      await fetch(`/api/chat/memories/${id}`, { method: "DELETE" });
      setMemories((m) => m.filter((x) => x.id !== id));
    } catch {
      // 删除失败静默，用户可重试
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

    try {
      const resp = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: text, user_id: uid, platform: "web" }),
      });
      const data = await resp.json();
      setMessages((m) => [
        ...m,
        {
          role: "ai",
          text: data.reply || "处理失败了，再试试？",
          time: new Date().toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" }),
        },
      ]);
      // 对话后刷新记忆（可能自动记了新东西）
      const memRes = await fetch(`/api/chat/memories?user_id=${uid}`);
      const memData = await memRes.json();
      setMemories(memData.memories || []);
    } catch (e) {
      setMessages((m) => [
        ...m,
        { role: "ai", text: "网络错误，稍后再试。", time: "" },
      ]);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-white/5 px-5 py-4">
        <h1 className="text-base font-semibold text-zinc-100">AI 助手</h1>
        <p className="mt-0.5 text-xs text-zinc-500">说人话就能用，发链接自动拆解</p>
      </div>

      {/* AI 记得你：记忆透明展示，可删除 */}
      {memories.length > 0 && (
        <div className="border-b border-white/5 px-5 py-3">
          <div className="mb-1.5 flex items-center justify-between">
            <span className="text-xs text-zinc-500">🧠 AI 记得你</span>
            <span className="text-[10px] text-zinc-600">点 × 可让我忘记</span>
          </div>
          <div className="flex flex-wrap gap-1.5">
            {memories.slice(0, 8).map((mem) => (
              <span
                key={mem.id}
                title={`${mem.type === "preference" ? "偏好" : mem.type === "feedback" ? "反馈" : "结晶"} · 重要度 ${mem.importance}`}
                className="group inline-flex items-center gap-1 rounded-full border border-white/10 bg-white/5 py-1 pl-2.5 pr-1 text-xs text-zinc-300"
              >
                {MEMORY_ICON[mem.type] || "📌"} {mem.content.length > 18 ? mem.content.slice(0, 18) + "…" : mem.content}
                <button
                  onClick={() => deleteMemory(mem.id)}
                  className="rounded-full px-1 text-zinc-500 transition-colors hover:bg-amber-400/20 hover:text-amber-300"
                  title="删除这条记忆"
                >
                  ×
                </button>
              </span>
            ))}
          </div>
        </div>
      )}

      <div ref={scrollRef} className="flex-1 space-y-4 overflow-y-auto px-5 py-4">
        {messages.map((m, i) => (
          <div key={i} className={`flex ${m.role === "user" ? "justify-end" : "justify-start"}`}>
            <div
              className={`max-w-[80%] rounded-2xl px-4 py-2.5 text-sm leading-relaxed ${
                m.role === "user"
                  ? "bg-amber-400 text-zinc-900"
                  : "bg-[#2a2e38] text-zinc-200"
              }`}
            >
              <div className="whitespace-pre-wrap">{m.text}</div>
              <div className={`mt-1 text-[10px] ${m.role === "user" ? "text-amber-900/60" : "text-zinc-500"}`}>
                {m.time}
              </div>
            </div>
          </div>
        ))}
        {loading && (
          <div className="flex justify-start">
            <div className="rounded-2xl bg-[#2a2e38] px-4 py-2.5 text-sm text-zinc-400">
              思考中...
            </div>
          </div>
        )}
      </div>

      <div className="border-t border-white/5 px-5 py-3">
        <div className="mb-2 flex flex-wrap gap-1.5">
          {["帮我拆解这个视频 https://v.douyin.com/xxx", "拆完了吗", "还有类似的吗", "记住我喜欢口播类"].map((q) => (
            <button
              key={q}
              onClick={() => setInput(q)}
              className="rounded-full border border-white/10 bg-white/5 px-3 py-1 text-xs text-zinc-400 transition-colors hover:border-amber-400/30 hover:text-amber-200"
            >
              {q.length > 12 ? q.slice(0, 12) + "..." : q}
            </button>
          ))}
        </div>
        <div className="flex gap-2">
          <input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && send()}
            placeholder="发视频链接，或问我问题..."
            className="flex-1 rounded-lg border border-white/10 bg-[#252932] px-4 py-2.5 text-sm text-zinc-200 placeholder-zinc-500 outline-none focus:border-amber-400/50"
          />
          <button
            onClick={send}
            disabled={loading || !input.trim()}
            className="rounded-lg bg-amber-400 px-5 py-2.5 text-sm font-medium text-zinc-900 transition-colors hover:bg-amber-300 disabled:opacity-50"
          >
            发送
          </button>
        </div>
      </div>
    </div>
  );
}
