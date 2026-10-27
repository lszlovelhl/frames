// 真折射窗口（取经 tomagranate/liquid-glass clone 模式）：
// 把主滚动区内容克隆到"玻璃背后"的 fixed 窗口，用 SVG feDisplacementMap 弯曲——
// 滚过底栏的真实内容（消息气泡等）像素被弯曲，这才是苹果 Liquid Glass 的"实时折射"
import { useEffect, useLayoutEffect, useRef, useState } from "react";

interface Props {
  /** 当前视图；视图切换时重建镜像 */
  view: string;
}

export default function LiquidGlassRefraction({ view }: Props) {
  const winRef = useRef<HTMLDivElement>(null);
  const innerRef = useRef<HTMLDivElement>(null);
  const [rect, setRect] = useState<{ top: number; height: number } | null>(null);

  // 窗口对齐底栏矩形（实时读取 nav rect，避免硬编码）
  useLayoutEffect(() => {
    const nav = document.querySelector("nav.liquid-glass");
    if (!nav) return;
    const update = () => {
      const r = nav.getBoundingClientRect();
      setRect({ top: r.top, height: r.height });
    };
    update();
    window.addEventListener("resize", update);
    window.addEventListener("scroll", update, true);
    return () => {
      window.removeEventListener("resize", update);
      window.removeEventListener("scroll", update, true);
    };
  }, []);

  // 克隆主滚动内容 + 滚动同步
  useEffect(() => {
    if (!rect) return;
    const scrollEl = document.querySelector('[data-scroll="main"]') as HTMLElement | null;
    const win = winRef.current;
    const inner = innerRef.current;
    if (!scrollEl || !win || !inner) return;

    let raf = 0;
    const syncPos = () => {
      const s = scrollEl.getBoundingClientRect();
      // 窗口内内容平移：让"滚到玻璃下的那一行"正好落在窗口位置
      const offset = s.top - rect.top - scrollEl.scrollTop;
      const left = s.left - win.getBoundingClientRect().left;
      inner.style.transform = `translate(${left}px, ${offset}px)`;
      inner.style.width = `${s.width}px`;
    };
    const clone = () => {
      inner.innerHTML = scrollEl.innerHTML;
      syncPos();
    };
    const onScroll = () => {
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(syncPos);
    };

    clone();
    const mo = new MutationObserver(clone);
    mo.observe(scrollEl, { childList: true, subtree: true, characterData: true });
    scrollEl.addEventListener("scroll", onScroll);
    return () => {
      cancelAnimationFrame(raf);
      mo.disconnect();
      scrollEl.removeEventListener("scroll", onScroll);
    };
  }, [rect, view]);

  if (!rect) return null;
  return (
    <div
      ref={winRef}
      className="lq-refraction"
      style={{
        position: "fixed",
        left: 12,
        right: 12,
        top: rect.top,
        height: rect.height,
        overflow: "hidden",
        filter: "url(#lq-lens)",
        pointerEvents: "none",
        zIndex: 45,
        borderRadius: "inherit",
      }}
      aria-hidden="true"
    >
      <div
        ref={innerRef}
        className="lq-backdrop"
        style={{ position: "absolute", top: 0, left: 0, willChange: "transform" }}
      />
    </div>
  );
}
