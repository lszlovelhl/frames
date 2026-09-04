export default function CreateView() {
  const steps = [
    { icon: "◈", title: "元素组合器", desc: "从元素库勾选卡片（钩子/结构/话术…），指定目标平台与意图，一键生成选题方向" },
    { icon: "▤", title: "脚本生成", desc: "基于元素 formula 产出脚本初稿：钩子-展开-CTA 完整结构，可逐段改写" },
    { icon: "✎", title: "分镜与拍摄指引", desc: "脚本转分镜表，输出拍摄清单、镜头建议、剪辑节奏提示" },
    { icon: "↗", title: "发布与回流", desc: "作品上线后拉取数据，回写元素库形成「什么元素真的有效」的飞轮" },
  ];
  return (
    <div className="mx-auto max-w-4xl px-8 py-8">
      <header className="mb-8">
        <h1 className="text-xl font-semibold text-zinc-100">创作台</h1>
        <p className="mt-1 text-sm text-zinc-500">把拆解提炼的元素，反哺成你自己的爆款脚本。本模块将在「元素库积累 → 创作 API」后开放。</p>
      </header>
      <div className="rounded-xl border border-dashed border-amber-300/20 bg-amber-300/[0.03] p-5 text-sm text-amber-200/80">
        开发进度：已落库 elements 表与创作域 3 张表（creations / creation_assets / creation_publishes），下一步实现创作 API。
      </div>
      <div className="mt-6 grid gap-3 sm:grid-cols-2">
        {steps.map((s) => (
          <div key={s.title} className="rounded-xl border border-white/5 bg-[#1c1f26] p-4 opacity-60">
            <div className="flex items-center gap-2 text-amber-200/70">
              <span>{s.icon}</span>
              <span className="text-sm font-medium text-zinc-200">{s.title}</span>
            </div>
            <p className="mt-1.5 text-xs leading-relaxed text-zinc-500">{s.desc}</p>
          </div>
        ))}
      </div>
    </div>
  );
}
