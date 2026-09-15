"""品类质量监测看板：按品类聚合所有已完成拆解的质量指标与内容厚度。

这是"时刻监测每个品类输出质量"的机制底座：每次拆解完成后运行一次，
就能看到每个品类的最新质量水位与历史稳定性（次数、达标率、各维度率、
元素产出量、内容厚度）。

指标口径（与 element_baseline 一致）：
- hard：硬违规率（应为 0）
- label / variant / mechanism / depth / evidence：soft 率（≤25% 达标）
- 厚度：取该品类最近一次拆解的 lib_* 元素机制/想象平均字数（深度化成效）

用法：
    .venv/bin/python scripts/quality_dashboard.py [--json] [--category 美妆]
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "frames.db"

LIB_TABLES = ("lib_topic", "lib_hook", "lib_copywriting", "lib_quote", "lib_method")


def _fmt_pct(x: float | None) -> str:
    if x is None:
        return "  -  "
    return f"{x * 100:4.0f}%"


def main() -> int:
    ap = argparse.ArgumentParser(description="品类质量监测看板")
    ap.add_argument("--json", action="store_true", help="输出 JSON 报告")
    ap.add_argument("--category", default=None, help="只看某个品类")
    args = ap.parse_args()

    if not DB_PATH.exists():
        print(f"数据库不存在：{DB_PATH}")
        return 2

    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row

    # 聚合每个 done job 的基线 + 对应视频品类
    jobs = c.execute(
        """
        SELECT j.id, j.video_id, j.script_id, j.created_at, j.evidence,
               COALESCE(v.category_guess, '未分类') AS category, v.title
        FROM breakdown_jobs j LEFT JOIN videos v ON v.id = j.video_id
        WHERE j.status = 'done'
        ORDER BY j.created_at
        """
    ).fetchall()

    per_cat: dict[str, dict] = {}
    for j in jobs:
        try:
            ev = json.loads(j["evidence"] or "{}")
        except json.JSONDecodeError:
            continue
        eb = ev.get("element_baseline") or {}
        if not eb.get("total"):
            continue
        cat = j["category"]
        row = per_cat.setdefault(
            cat,
            {
                "category": cat,
                "jobs": 0,
                "pass_count": 0,
                "elements_total": 0,
                "rates_sum": {},
                "latest_mechanism_len": None,
                "latest_imagination_len": None,
                "latest_at": "",
                "latest_title": "",
            },
        )
        row["jobs"] += 1
        row["elements_total"] += eb.get("total") or 0
        if eb.get("pass"):
            row["pass_count"] += 1
        for k, v in (eb.get("rates") or {}).items():
            row["rates_sum"][k] = row["rates_sum"].get(k, 0.0) + float(v or 0)
        # 内容厚度：用该 job 关联 script 的 lib_* 平均字段长度
        if j["script_id"]:
            lens = _field_lens(c, j["script_id"])
            if lens:
                row["latest_mechanism_len"] = lens[0]
                row["latest_imagination_len"] = lens[1]
        row["latest_at"] = str(j["created_at"])[:16]
        row["latest_title"] = (j["title"] or "")[:20]

    cats = sorted(per_cat.values(), key=lambda r: (-r["jobs"], r["category"]))
    if args.category:
        cats = [r for r in cats if r["category"] == args.category]

    out = []
    for r in cats:
        n = max(1, r["jobs"])
        rates = {k: round(v / n, 4) for k, v in r["rates_sum"].items()}
        rec = {
            "category": r["category"],
            "jobs": r["jobs"],
            "pass": f"{r['pass_count']}/{r['jobs']}",
            "elements_avg": round(r["elements_total"] / n, 1),
            "rates": rates,
            "mechanism_len_avg": r["latest_mechanism_len"],
            "imagination_len_avg": r["latest_imagination_len"],
            "latest_at": r["latest_at"],
            "latest_title": r["latest_title"],
        }
        out.append(rec)

    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0

    if not out:
        print("暂无已完成的拆解数据。")
        return 0

    hdr = (
        f"{'品类':<10}{'拆解':>5}{'达标':>8}{'元素均':>7}"
        f"{'hard':>7}{'观后感':>7}{'变体':>7}{'机制':>7}{'深度':>7}"
        f"{'机制均字':>8}{'想象均字':>8}  最近"
    )
    print("=" * len(hdr))
    print(hdr)
    print("=" * len(hdr))
    for r in out:
        rt = r["rates"]
        print(
            f"{r['category']:<10}{r['jobs']:>5}{r['pass']:>8}"
            f"{r['elements_avg']:>7.1f}"
            f"{_fmt_pct(rt.get('hard')):>7}{_fmt_pct(rt.get('label')):>7}"
            f"{_fmt_pct(rt.get('variant')):>7}{_fmt_pct(rt.get('mechanism')):>7}"
            f"{_fmt_pct(rt.get('depth')):>7}"
            f"{str(r['mechanism_len_avg'] or '-'):>8}{str(r['imagination_len_avg'] or '-'):>8}  "
            f"{r['latest_at']} {r['latest_title']}"
        )
    print("=" * len(hdr))
    print("口径：hard=0 且 观后感/变体/机制/深度 soft 率 ≤25% 为达标；厚度取该品类最近一次拆解均值")
    return 0


def _field_lens(c: sqlite3.Connection, script_id: str) -> tuple[int, int] | None:
    """取某 script 关联元素的最新机制/想象平均字数（跨 5 类元素库）。"""
    m_s, i_s = [], []
    for t in LIB_TABLES:
        try:
            rows = c.execute(
                f"SELECT mechanism, imagination FROM {t} WHERE id IN "
                "(SELECT element_id FROM ref_element_source WHERE source_script_id=? AND element_table=?)",
                (script_id, t),
            ).fetchall()
        except sqlite3.Error:
            continue
        for r in rows:
            if r[0]:
                m_s.append(len(str(r[0])))
            if r[1]:
                i_s.append(len(str(r[1])))
    if not m_s:
        return None
    return round(sum(m_s) / len(m_s)), round(sum(i_s) / len(i_s)) if i_s else None


if __name__ == "__main__":
    sys.exit(main())
