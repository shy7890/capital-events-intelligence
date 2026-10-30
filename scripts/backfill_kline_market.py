"""全市场日K回填（一次性夜间任务）：对 stock 表全部代码补 kline_daily，断点续传。

用法：.venv/bin/python scripts/backfill_kline_market.py [--start 2024-06-01]
进度：config 表 "kline_backfill" = {"done":[代码...], "failed":{代码:错误}}，每 100 只落一次。
重复执行安全：已在 done 的跳过；upsert 幂等。
"""

from __future__ import annotations

import argparse
import sys
from datetime import date

sys.path.insert(0, "src")

from zhuang_monitor import db, ingest
from zhuang_monitor.sources import ak_adapters, baostock_k

PROGRESS_KEY = "kline_backfill"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2024-06-01")
    args = ap.parse_args()
    today = date.today().isoformat()

    conn = db.connect()
    db.init_schema(conn)
    codes = [r["code"] for r in conn.execute(
        "SELECT code FROM stock ORDER BY code").fetchall()]
    prog = ingest.get_config(conn, PROGRESS_KEY, {"done": [], "failed": {}})
    with_rows = {r[0] for r in conn.execute("SELECT DISTINCT code FROM kline_daily").fetchall()}
    done = {c for c in prog["done"] if c in with_rows}  # 睡眠期假完成的代码重算剔除
    prog["failed"] = {k: v for k, v in prog["failed"].items() if k not in done}
    todo = [c for c in codes if c not in done]
    print(f"全市场 {len(codes)} 只，已完成 {len(done)}，本次待补 {len(todo)}", flush=True)

    ok = fail = 0
    for i, code in enumerate(todo, 1):
        try:
            rows = baostock_k.kline_rows(code, args.start, today)
        except Exception:
            try:
                rows = ak_adapters.kline_sina_rows(code, args.start, today)
            except Exception as exc:  # noqa: BLE001
                prog["failed"][code] = f"{type(exc).__name__}: {exc}"[:120]
                fail += 1
                if fail % 50 == 0:
                    ingest.set_config(conn, PROGRESS_KEY, prog)
                continue
        if not rows:
            prog["failed"][code] = "无数据(停牌/退市/网络窗口)"
            fail += 1
            continue
        ingest.upsert_kline(conn, rows)
        done.add(code)
        prog["failed"].pop(code, None)
        prog["done"] = sorted(done)
        ok += 1
        if i % 100 == 0:
            ingest.set_config(conn, PROGRESS_KEY, prog)
            print(f"进度 {i}/{len(todo)}（成功{ok} 失败{fail}）", flush=True)
    ingest.set_config(conn, PROGRESS_KEY, prog)
    ingest.record_scan_run(conn, today, "kline_market_backfill",
                           "green" if fail == 0 else "red",
                           stats={"ok": ok, "fail": fail},
                           errors=None if not prog["failed"] else str(list(prog["failed"].items())[:20]))
    total_rows = conn.execute("SELECT COUNT(*) FROM kline_daily").fetchone()[0]
    n_codes = conn.execute("SELECT COUNT(DISTINCT code) FROM kline_daily").fetchone()[0]
    print(f"完成：本次成功{ok} 失败{fail}；kline_daily 共 {total_rows} 行 / {n_codes} 只", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
