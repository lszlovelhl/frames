// 液态玻璃滑块（可拖拽）—— iOS 26 风格，轨道 + 玻璃滑块头
interface LiquidSliderProps {
  value: number;
  min?: number;
  max?: number;
  step?: number;
  onChange: (v: number) => void;
  ariaLabel?: string;
}

export default function LiquidSlider({
  value,
  min = 0,
  max = 100,
  step = 1,
  onChange,
  ariaLabel,
}: LiquidSliderProps) {
  const pct = ((value - min) / (max - min)) * 100;
  return (
    <div className="flex items-center gap-3">
      <input
        type="range"
        className="liquid-slider flex-1"
        min={min}
        max={max}
        step={step}
        value={value}
        aria-label={ariaLabel}
        onChange={(e) => onChange(Number(e.target.value))}
        style={{
          background: `linear-gradient(90deg, rgba(10,132,255,0.85) 0%, rgba(10,132,255,0.85) ${pct}%, rgba(255,255,255,0.12) ${pct}%)`,
        }}
      />
    </div>
  );
}
