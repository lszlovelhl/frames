"""元素质量基线验收检查脚本。

对库里最近 N 条已完成的三层拆解（breakdown_jobs.evidence.element_baseline）
输出批量验收报告：每条拆解的元素质量是否达到基线，并按维度汇总达标率。

口径（element_baseline）：hard=0 且 观后感/变体/机制 soft 率 ≤ 25%。
证据链（原话+秒数）与结构严校验走 hard，创意可发散（soft 容忍 25%）。

用法：
    .venv/bin/python scripts/check_element_baseline.py [--limit 10] [--json]
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "frames.db"


def main() -> int:
    ap = argparse.ArgumentParser(description="元素质量基线验收")
    ap.add_argument("--limit", type=int, default=10, help="最近 N 条拆解（默认 10）")
    ap.add_argument("--json", action="store_true", help="输出 JSON 报告")
    args = ap.parse_args()

    if not DB_PATH.exists():
        print(f"[错误] 数据库不存在：{DB_PATH}")
        return 1

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT id, video_id, status, evidence, substr(COALESCE(finished_at,''),1,19) AS finished_at
        FROM breakdown_jobs
        WHERE status='done' AND evidence IS NOT NULL
        ORDER BY COALESCE(finished_at, created_at) DESC
        LIMIT ?
        """,
        (args.limit,),
    ).fetchall()
    conn.close()

    if not rows:
        print("无已完成拆解可验收")
        return 1

    report: list[dict] = []
    passed = 0
    for r in rows:
        try:
            ev = json.loads(r["evidence"] or "{}")
        except json.JSONDecodeError:
            ev = {}
        eb = ev.get("element_baseline")
        if eb is None:
            entry = {
                "job_id": r["id"][:8],
                "video_id": r["video_id"][:8],
                "finished_at": r["finished_at"],
                "baseline": "N/A（基线接入前的拆解，需重跑才出验收）",
            }
        else:
            if eb.get("pass"):
                passed += 1
            entry = {
                "job_id": r["id"][:8],
                "video_id": r["video_id"][:8],
                "finished_at": r["finished_at"],
                "pass": eb.get("pass"),
                "reason": eb.get("reason"),
                "total": eb.get("total"),
                "by_type": eb.get("by_type"),
                "rates": eb.get("rates"),
                "hard_rows": eb.get("hard_rows"),
                "label_rows": eb.get("label_rows"),
                "variant_rows": eb.get("variant_rows"),
                "mechanism_rows": eb.get("mechanism_rows"),
            }
        report.append(entry)

    if args.json:
        print(json.dumps({"rule": "hard=0 且 观后感/变体/机制 soft 率 ≤ 25%", "items": report}, ensure_ascii=False, indent=2))
        return 0

    print("=" * 68)
    print("元素质量基线验收报告")
    print("口径：hard=0 且 观后感/变体/机制 soft 率 ≤ 25%")
    print("=" * 68)
    for e in report:
        print(f"\n[{e['job_id']}] 完成于 {e.get('finished_at')}")
        if "pass" not in e:
            print(f"  {e['baseline']}")
            continue
        mark = "✅ 达标" if e["pass"] else "❌ 未达标"
        print(f"  {mark} | 元素 {e['total']} 条 | {e['reason']}")
        print(f"  构成: {e['by_type']}")
        print(f"  比率: hard={e['rates']['hard']:.0%} 观后感={e['rates']['label']:.0%} "
              f"变体={e['rates']['variant']:.0%} 机制={e['rates']['mechanism']:.0%} "
              f"证据={e['rates']['evidence']:.0%}")
        for name, key in (("hard 违规", "hard_rows"), ("观后感", "label_rows"),
                          ("变体不足", "variant_rows"), ("机制不足", "mechanism_rows")):
            items = e.get(key) or []
            if items:
                print(f"  {name}: " + "; ".join(f"{i['row_type']}:{i['label']}({i['issues'][0][:40]})" for i in items[:3]))
    scored = [e for e in report if "pass" in e]
    if scored:
        print(f"\n{'=' * 68}")
        print(f"达标 {passed}/{len(scored)}（{passed / len(scored):.0%}）")
    return 0 if not scored or passed == len(scored) else 2


if __name__ == "__main__":
    sys.exit(main())
