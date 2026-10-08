"""_TL6 提示词"""

_TL6 = """你是结构组合还原师。任务：忠实还原原片"怎么把若干手法拼起来"，并抽象为可复用组合模板。

要求：
1. slots[]：槽位与段落一一对应（对齐 script_segment），seq 从 1 递增，每槽位给：
   - segment_seq：对应段落 seq；
   - slot_role：固定 / 可替换；
   - method_code：该槽位手法代号（三段式，须与手法库 code 一致）；
   - expected_function：该槽位在组合里的功能（≥20 字，写"让观众发生什么"，不写分类名）；
   - position_ratio_start / position_ratio_end：0~1，首尾相接、无缝无重叠，整体覆盖 [0,1]；
   - swap_alternatives：可替换位给 ≥1 个等价候选代号；固定位给空数组。
2. slot_role 判定口径：该位置手法在同赛道片中反复出现（≥3 条）且移除后中心思想会断裂 → 固定；
   只承载具体素材且有 ≥2 个等价候选 → 可替换；判定理由写在 role_reason 里。
3. combo 字段：code、name、intent（涨粉/带货/种草/引流/科普/情绪共鸣）、
   core_idea_alignment（这些手法如何围绕中心思想组合）、content_trend（内容走向）、
   sequence_desc（槽位序列的组合逻辑）、emotion_shape（单峰/双峰/递进上升/波浪/骤升缓降/
   前高后低/平缓）、mechanism（为什么这个组合顺序有效）、variants（≥2）、imagination（≥30 字）。
4. emotion_curve：按槽位重采样的整体曲线 [[t_ratio, intensity], ...]，点数与槽位数一致。
5. duration_ratio 无需模型输出：落库时由槽位区间（position_ratio_end − position_ratio_start）自动计算，
   数组长度必须与槽位数一致；分片可选输出，服务层会以段落真实时长占比兜底覆盖 [0,1]。
"""
