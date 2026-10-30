"""M4 因子扫描 CLI（流水线 B）：对"有 K 线数据的宇宙"跑当日全量并打印榜单。

用法：.venv/bin/python scripts/scan_factors.py [--date YYYY-MM-DD] [--limit-report 10] [--probe 123456]
scan_run 记账（stage="scan"）由 pipeline.backfill_scan 完成。全程读库计算，零联网。
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from zhuang_monitor import db, pipeline, scanner  # noqa: E402


def _print_report(conn: sqlite3.Connection, day: str, top: int) -> None:
    rows = conn.execute(
        "SELECT sr.code, s.name, sr.circ_mv, sr.gain_pct, sr.holder_drop_pct, "
        "sr.resonance, sr.tier, sr.hits_json, sr.start_date, sr.start_age_days "
        "FROM scan_result sr LEFT JOIN stock s ON sr.code = s.code "
        "WHERE sr.scan_date = ? ORDER BY "
        "CASE sr.tier WHEN 'S' THEN 0 WHEN 'A' THEN 1 WHEN 'B' THEN 2 ELSE 3 END, "
        "sr.resonance DESC, sr.gain_pct DESC LIMIT ?",
        (day, top),
    ).fetchall()
    tiers = conn.execute(
        "SELECT tier, COUNT(*) AS n FROM scan_result WHERE scan_date = ? GROUP BY tier",
        (day,),
    ).fetchall()
    print(f"扫描日 {day} | tier 分布: " +
          "  ".join(f"{t['tier'] or '无'}={t['n']}" for t in tiers))
    print(f"Top {min(top, len(rows))} 疑似庄股榜:")
    print(f"{'代码':<8}{'名称':<10}{'涨幅%':>8}{'户数降幅%':>10}{'市值亿':>8}"
          f"{'距今':>5}{'共振':>4}  层级  命中项")
    for r in rows:
        drop = "" if r["holder_drop_pct"] is None else f"{r['holder_drop_pct']:.1f}"
        age = "" if r["start_age_days"] is None else str(r["start_age_days"])
        print(f"{r['code']:<8}{(r['name'] or '?'):<10}{r['gain_pct']:>8.1f}{drop:>10}"
              f"{r['circ_mv']:>8.1f}{age:>5}{r['resonance']:>4}  {r['tier'] or '-'}    "
              f"{r['hits_json']}")


def _probe(conn: sqlite3.Connection, code: str, day: str) -> None:
    det = scanner.explain(conn, code, today=day)
    print(f"\n--probe {code} 因子实测（基准日 {day}）：")
    for key in ("start_date", "start_age_days", "gain_pct", "lhb_after_start",
                "holder_drop_pct", "circ_mv", "avg_amount", "weak_types", "hits",
                "resonance", "tier", "listed", "excluded_reason"):
        print(f"  {key} = {det[key]}")


def main() -> int:
    ap = argparse.ArgumentParser(description="M4 因子扫描（流水线 B，读库计算）")
    ap.add_argument("--date", default=date.today().isoformat(),
                    help="扫描基准日（默认今天）")
    ap.add_argument("--limit-report", type=int, default=10, help="榜单打印条数")
    ap.add_argument("--probe", metavar="CODE", help="额外打印单票各因子实测明细")
    args = ap.parse_args()

    conn = db.connect()
    db.init_schema(conn)

    n_kline = conn.execute("SELECT COUNT(DISTINCT code) FROM kline_daily").fetchone()[0]
    n_stock = conn.execute("SELECT COUNT(*) FROM stock").fetchone()[0]
    if n_kline < n_stock:
        print(f"提示: kline_daily 仅覆盖 {n_kline}/{n_stock} 只（全市场 K 线由夜间批任务"
              f"回填）；本轮只对『有 K 线的宇宙』计算因子，universe_size 将如实反映该范围。")

    res = pipeline.backfill_scan(conn, today=args.date, run_date=args.date)
    if not res["ok"]:
        print(f"FAIL: scan 阶段红单：{res.get('error')}")
        return 1
    print(f"扫描完成: universe_size={res['universe_size']} scanned={res['scanned']} "
          f"hits={res['hits']} upgraded_watch={res['upgraded_watch']} "
          f"resonance_updated={res['resonance_updated']} "
          f"archived={res.get('archived', 0)} "
          f"tier_counts={res['tier_counts']}")
    _print_report(conn, args.date, args.limit_report)
    if args.probe:
        _probe(conn, args.probe, args.date)
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
