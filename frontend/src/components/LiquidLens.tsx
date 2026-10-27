// 液态玻璃真折射透镜（取经 tomagranate/liquid-glass 架构）
// SVG feDisplacementMap + 圆角矩形 SDF 位移映射：
// 弯曲集中在玻璃边缘 rim（深度 depth），中心归零 —— 清晰中心 + 折射斜面，像真实曲面玻璃
// 附加：双通道色差（红/蓝边缘条纹）+ specular 边缘高光
import { useEffect } from "react";

const FILTER_ID = "lq-lens";

export default function LiquidLens() {
  useEffect(() => {
    // 生成 SDF 位移映射 PNG：R/G 编码 x/y 位移（128=不动），B 携带 specular
    const SIZE = 96;
    const RADIUS = 28;   // 与滑块圆角匹配（圆角透镜）
    const DEPTH = 10;    // rim 厚度
    const AMP = 56;      // 最大位移（px 级，filter scale 再放大）
    const c = document.createElement("canvas");
    c.width = c.height = SIZE;
    const g = c.getContext("2d")!;
    const img = g.createImageData(SIZE, SIZE);
    const cx = SIZE / 2;
    const cy = SIZE / 2;
    const hw = SIZE / 2;
    const hh = SIZE / 2;

    function sdRoundRect(px: number, py: number): number {
      const dx = Math.abs(px - cx) - (hw - RADIUS);
      const dy = Math.abs(py - cy) - (hh - RADIUS);
      const ox = Math.max(dx, 0);
      const oy = Math.max(dy, 0);
      return Math.hypot(ox, oy) + Math.min(Math.max(dx, dy), 0) - RADIUS;
    }

    for (let y = 0; y < SIZE; y++) {
      for (let x = 0; x < SIZE; x++) {
        const i = (y * SIZE + x) * 4;
        const d = sdRoundRect(x + 0.5, y + 0.5);
        const dist = Math.hypot(x - cx, y - cy) || 1;
        // rim 内弯曲，向中心渐消；位移方向沿径向向外（凸透镜感）
        const t = 1 - Math.min(Math.abs(d) / (DEPTH / 2), 1);
        const amp = d > 0 ? AMP * t * 0.6 : AMP * t; // 外缘略弱，内缘强（斜面）
        const dx = ((x - cx) / dist) * amp;
        const dy = ((y - cy) / dist) * amp;
        const spec = Math.max(0, 1 - Math.abs(d) / (DEPTH / 2)) * 255; // rim 高光
        img.data[i] = 128 + dx;
        img.data[i + 1] = 128 + dy;
        img.data[i + 2] = spec;
        img.data[i + 3] = 255;
      }
    }
    g.putImageData(img, 0, 0);
    const href = c.toDataURL("image/png");

    // 注入 <feImage>（固定 id；映射只在形状变化时重生成，此处形状恒定）
    const fe = document.getElementById(FILTER_ID) as SVGFEImageElement | null;
    if (fe) fe.setAttribute("href", href);
  }, []);

  return (
    <svg width="0" height="0" style={{ position: "absolute" }} aria-hidden="true">
      <defs>
        <filter id={FILTER_ID} x="-20%" y="-20%" width="140%" height="140%" colorInterpolationFilters="sRGB">
          <feImage id={FILTER_ID} result="map" />
          {/* 双通道色差（极克制）：R 通道位移大（红边外偏）、B 通道位移小（蓝边内偏） */}
          <feDisplacementMap in="SourceGraphic" in2="map" scale="3" xChannelSelector="R" yChannelSelector="G" result="shiftR" />
          <feDisplacementMap in="SourceGraphic" in2="map" scale="1.5" xChannelSelector="R" yChannelSelector="G" result="shiftB" />
          <feColorMatrix in="shiftR" result="rC" values="1 0 0 0 0  0 0 0 0 0  0 0 0 0 0  0 0 0 1 0" />
          <feColorMatrix in="shiftB" result="bC" values="0 0 0 0 0  0 0 0 0 0  0 0 1 0 0  0 0 0 1 0" />
          <feComposite in="rC" in2="bC" operator="arithmetic" k1="0" k2="1" k3="1" k4="0" result="fringe" />
          {/* specular rim：位移映射亮度 → alpha，叠加中性白光（不染色） */}
          <feColorMatrix in="map" result="specA" type="luminanceToAlpha" />
          <feFlood flood-color="white" flood-opacity="0.8" result="white" />
          <feComposite in="white" in2="specA" operator="in" result="spec" />
          <feComposite in="fringe" in2="spec" operator="arithmetic" k1="0" k2="1" k3="0.26" k4="0" />
        </filter>
      </defs>
    </svg>
  );
}
