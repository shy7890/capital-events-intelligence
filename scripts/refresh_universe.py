"""全市场股票档案同步：baostock 在市列表 → stock 表（名称/板块/上市日），腾讯分批补市值。

用法：.venv/bin/python scripts/refresh_universe.py [YYYY-MM-DD]（交易日默认最近工作日回退）
幂等：按 code upsert；可每日批任务前运行。北交所等 v1 排除板块自动跳过。
"""

from __future__ import annotations

import sys
from datetime import date, timedelta

sys.path.insert(0, "src")

from zhuang_monitor import db, ingest, pipeline
from zhuang_monitor.sources import baostock_k, tencent


def recent_trading_day() -> str:
    today = date.today()
    for delta in range(0, 10):
        d = (today - timedelta(days=delta)).isoformat()
        try:
            rows = baostock_k.all_stocks(d)
            if rows:
                return d, rows
        except Exception:  # noqa: BLE001
            continue
    raise SystemExit("baostock 近 10 日均无法取到在市列表")


def main() -> int:
    if len(sys.argv) > 1:
        day = sys.argv[1]
        rows = baostock_k.all_stocks(day)
    else:
        day, rows = recent_trading_day()

    basic_by_bs = {}
    want: list[dict] = []
    for r in rows:
        code = r["code"]
        board = baostock_k.board_for(code)
        if board is None:
            continue
        want.append({"code": code, "name": r["name"], "board": board,
                     "list_date": None, "is_st": 1 if "ST" in r["name"].upper() else 0,
                     "total_mv": None, "circ_mv": None})
    print(f"{day} 在市 {len(rows)} 只，纳入板块范围 {len(want)} 只", flush=True)
    conn = db.connect()
    db.init_schema(conn)
    ingest.upsert_stock(conn, want)

    codes = [w["code"] for w in want]
    print("拉取上市日期（baostock 全量一次）……", flush=True)
    try:
        basics = baostock_k.all_basic()
        basics = [b for b in basics if baostock_k.board_for(b["code"]) is not None]  # 排除北交所等
        ingest.upsert_stock(conn, [{"code": b["code"], "name": b["name"], "board": None,
                                    "list_date": b["list_date"], "total_mv": None, "circ_mv": None,
                                    "is_st": 1 if "ST" in b["name"].upper() else 0}
                                   for b in basics])
        print(f"  已补 {len(basics)} 只", flush=True)
    except Exception as exc:  # noqa: BLE001
        print(f"  上市日期失败：{exc}", flush=True)

    print("腾讯市值快照……", flush=True)
    try:
        snap = tencent.snapshot(codes)
        ingest.upsert_stock(conn, [{"code": c, "name": None, "board": None, "list_date": None,
                                    "is_st": None, "total_mv": v.get("total_mv"), "circ_mv": v.get("circ_mv")}
                                   for c, v in snap.items()])
        print(f"  市值更新 {len(snap)} 只", flush=True)
    except Exception as exc:  # noqa: BLE001
        print(f"  市值快照失败（不影响档案）：{exc}", flush=True)

    r = pipeline._run_stage(conn, date.today().isoformat(), "universe",
                            lambda: {"day": day, "stocks": len(want)})
    total = conn.execute("SELECT COUNT(*) FROM stock").fetchone()[0]
    with_mv = conn.execute("SELECT COUNT(*) FROM stock WHERE circ_mv IS NOT NULL").fetchone()[0]
    print(f"完成：stock 表 {total} 行（有市值 {with_mv}）", flush=True)
    conn.close()
    return 0 if r["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
