"""全市场股东户数定向回填（东财数据中心，经 pace 串行闸门）：断点续传，幂等。

用法：.venv/bin/python scripts/backfill_holder_market.py [--source scan|pool|both]
  scan=当日榜单命中票（默认）｜pool=候选池全部｜both=并集
进度：config 表 "holder_backfill" = {"done":[...], "failed":{code:错误}}，每 50 只落一次。
东财 403 封禁信号 → 立即熔断退出（红色 scan_run），稍后重跑即续传。
"""

from __future__ import annotations

import argparse
import sys
from datetime import date

sys.path.insert(0, "src")

from zhuang_monitor import db, ingest  # noqa: E402
from zhuang_monitor.em_throttle import EastmoneyBlocked  # noqa: E402
from zhuang_monitor.sources import ak_adapters  # noqa: E402

PROGRESS_KEY = "holder_backfill"


def _universe(conn, source: str, today: str) -> list[str]:
    codes: set[str] = set()
    if source in ("scan", "both"):
        codes |= {r["code"] for r in conn.execute(
            "SELECT DISTINCT code FROM scan_result WHERE scan_date = ?", (today,)).fetchall()}
    if source in ("pool", "both"):
        codes |= {r["code"] for r in conn.execute("SELECT DISTINCT code FROM pool_entry").fetchall()}
    return sorted(codes)


def main() -> int:
    ap = argparse.ArgumentParser(description="定向回填 holder_num（东财 gdhs_detail，限速串行）")
    ap.add_argument("--source", choices=["scan", "pool", "both"], default="scan",
                    help="回填范围（默认：当日榜单命中票）")
    args = ap.parse_args()
    today = date.today().isoformat()

    conn = db.connect()
    db.init_schema(conn)
    codes = _universe(conn, args.source, today)
    prog = ingest.get_config(conn, PROGRESS_KEY, {"done": [], "failed": {}})
    with_rows = {r[0] for r in conn.execute("SELECT DISTINCT code FROM holder_num").fetchall()}
    done = {c for c in prog["done"] if c in with_rows}
    prog["failed"] = {k: v for k, v in prog["failed"].items() if k not in done}
    todo = [c for c in codes if c not in done]
    print(f"目标 {len(codes)} 只，已完成 {len(done)}，本次待补 {len(todo)}", flush=True)

    ok = fail = 0
    aborted = ""
    for i, code in enumerate(todo, 1):
        try:
            rows = ak_adapters.holder_num_rows(code)
        except EastmoneyBlocked as exc:
            aborted = f"EastmoneyBlocked: {exc}"
            print(f"!! 东财 403 熔断，已停（进度已保存）：{exc}", flush=True)
            break
        except Exception as exc:  # noqa: BLE001
            prog["failed"][code] = f"{type(exc).__name__}: {exc}"[:120]
            fail += 1
            if fail % 50 == 0:
                ingest.set_config(conn, PROGRESS_KEY, prog)
            continue
        if not rows:
            prog["failed"][code] = "无数据(未披露)"
            fail += 1
            continue
        ingest.upsert_holder_num(conn, rows)
        done.add(code)
        prog["failed"].pop(code, None)
        prog["done"] = sorted(done)
        ok += 1
        if i % 50 == 0:
            ingest.set_config(conn, PROGRESS_KEY, prog)
            print(f"进度 {i}/{len(todo)}（成功{ok} 失败{fail}）", flush=True)
    ingest.set_config(conn, PROGRESS_KEY, prog)
    ingest.record_scan_run(conn, today, "holder_market_backfill",
                           "green" if not aborted and fail == 0 else "red",
                           stats={"ok": ok, "fail": fail, "aborted": aborted or None},
                           errors=aborted or (None if not prog["failed"]
                                              else str(list(prog["failed"].items())[:20])))
    n = conn.execute("SELECT COUNT(DISTINCT code) FROM holder_num").fetchone()[0]
    print(f"完成：本次成功{ok} 失败{fail} 熔断={'是' if aborted else '否'}；holder_num 现有 {n} 只", flush=True)
    return 1 if aborted else 0


if __name__ == "__main__":
    sys.exit(main())
