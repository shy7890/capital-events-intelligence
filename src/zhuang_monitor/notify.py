"""M5 提醒接线（PRD F6/Q8）：notification 表是唯一事实源，本模块只做 macOS 触达。

防刷屏三道闸（缺一不推送）：
① 日期闸：只推「ts 日期 == 今天」的通知——历史回填/重建产生成百上千条旧通知，一律静默；
② 配置闸：config 表 notify_macos 总开关（默认 1，UI 可关，PRD F10）；
③ 环境闸：ZM_NOTIFY_OFF=1（pytest/无头环境全局静音）。
任何失败只打 stderr，绝不打断批任务。
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from datetime import date

__all__ = ["should_push", "push", "maybe_push", "record_once"]


def _esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def should_push(conn: sqlite3.Connection | None, ts: str | None,
                *, today: str | None = None) -> bool:
    if not ts:
        return False
    if os.environ.get("ZM_NOTIFY_OFF") == "1":
        return False
    if str(ts)[:10] != (today or date.today().isoformat()):
        return False
    if conn is not None:
        from . import ingest

        if not ingest.get_config(conn, "notify_macos", 1):
            return False
    return True


def push(title: str, body: str) -> bool:
    if sys.platform != "darwin":
        return False
    script = (
        f'display notification "{_esc(body)}" '
        f'with title "{_esc(title)}" subtitle "资本事件研究台（提醒非决策）"'
    )
    try:
        r = subprocess.run(["/usr/bin/osascript", "-e", script],
                           capture_output=True, timeout=10, check=False)
        return r.returncode == 0
    except Exception as e:  # noqa: BLE001 —— 触达失败不影响落库
        print(f"[notify] osascript 失败（已忽略）: {e}", file=sys.stderr)
        return False


def maybe_push(conn: sqlite3.Connection | None, ts: str | None,
               title: str, body: str, *, today: str | None = None) -> bool:
    if not should_push(conn, ts, today=today):
        return False
    return push(title, body)


def record_once(conn: sqlite3.Connection, ts: str, level: str,
                title: str, body: str, *, today: str | None = None) -> bool:
    """把提醒写入唯一事实源，并按标题+正文去重后再尝试系统触达。

    变化评估可能在同一天重复运行，也可能在后续触发点重复看到同一条件；
    用稳定的标题和正文做自然键，避免把持续状态刷成重复提醒。历史日期仍落库，
    但沿用日期闸不会弹出 macOS 通知。
    """
    exists = conn.execute(
        "SELECT 1 FROM notification WHERE title=? AND body=? LIMIT 1",
        (title, body),
    ).fetchone()
    if exists:
        return False
    conn.execute(
        "INSERT INTO notification (ts, level, title, body, is_read) VALUES (?,?,?,?,0)",
        (ts, level, title, body),
    )
    maybe_push(conn, ts, title, body, today=today)
    return True
