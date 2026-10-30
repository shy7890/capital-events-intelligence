"""M5 AI 素材包导出 CLI（PRD F8）：单票一键生成 Markdown + JSON 素材包。

用法：.venv/bin/python scripts/export_material.py --code <6位代码> [--outdir data/exports]
全程只读计算，不写业务表；重复运行只会重新生成当日文件（文件名含日期）。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from zhuang_monitor import db, material  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="单票 AI 素材包导出（MD+JSON）")
    ap.add_argument("--code", required=True, help="6 位股票代码，如 123456")
    ap.add_argument("--outdir", default=None, help="输出目录（默认 data/exports/）")
    args = ap.parse_args()

    conn = db.connect()
    db.init_schema(conn)
    out = material.export_files(conn, args.code, args.outdir)
    conn.close()
    if out is None:
        print(f"FAIL: 库内没有 {args.code} 的任何数据（档案/公告/事件/K线全空）")
        return 1
    md_path, json_path = out
    print(f"OK  Markdown: {md_path}")
    print(f"OK  JSON:     {json_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
