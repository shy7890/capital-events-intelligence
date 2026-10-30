"""M6 数据对账（PRD §7 可观测 / §10 风险应对）：同日同票两源公告召回差异，供人工复核。

只读计算、零联网。cninfo 与 akshare_dc 的公告标题归一化后按自然键比对：
归一化=去首尾空白 + 去公司简称前缀（"示例公司:"/"示例公司："）+ 折叠连续空白。
仅统计「两源都覆盖到该票当日」的行，单边未回填不计为差异（避免假漏报）。
"""

from __future__ import annotations

import re
import sqlite3
from typing import Any

_COMPACT = re.compile(r"\s+")


def normalize_title(title: str, name: str | None = None) -> str:
    t = (title or "").strip()
    if name:
        nm = name.strip()
        if nm:
            t = re.sub(rf"^{re.escape(nm)}[:：]", "", t)
    return _COMPACT.sub("", t)


def pool_codes(conn: sqlite3.Connection) -> list[str]:
    return [r["code"] for r in conn.execute(
        "SELECT DISTINCT code FROM pool_entry ORDER BY code").fetchall()]


def recon_day(conn: sqlite3.Connection, day: str,
              codes: list[str] | None = None) -> dict[str, Any]:
    """返回 {date, universe, compared, rows:[{code,name,only_cninfo,only_dc,both}]}。"""
    if codes is None:
        codes = pool_codes(conn)
    rows: list[dict[str, Any]] = []
    compared = 0
    for code in codes:
        name = conn.execute(
            "SELECT name FROM stock WHERE code=?", (code,)).fetchone()
        nm = name["name"] if name else None
        per_src: dict[str, set[str]] = {}
        for r in conn.execute(
                "SELECT source, title FROM announcement"
                " WHERE code=? AND pub_date=? AND source IN ('cninfo','akshare_dc')",
                (code, day)).fetchall():
            per_src.setdefault(r["source"], set()).add(normalize_title(r["title"], nm))
        cn, dc = per_src.get("cninfo", set()), per_src.get("akshare_dc", set())
        if not cn and not dc:
            continue
        compared += 1
        rows.append({
            "code": code, "name": nm,
            "only_cninfo": sorted(cn - dc), "only_dc": sorted(dc - cn),
            "both": len(cn & dc),
        })
    return {"date": day, "universe": len(codes), "compared": compared, "rows": rows}


def available_dates(conn: sqlite3.Connection, limit: int = 30) -> list[str]:
    """有 akshare_dc（全市场）公告的日期，倒序——对账候选日。"""
    return [r["pub_date"] for r in conn.execute(
        "SELECT pub_date FROM announcement WHERE source='akshare_dc'"
        " GROUP BY pub_date ORDER BY pub_date DESC LIMIT ?", (limit,)).fetchall()]
