import { useEffect, useState } from "react";
import { fetchPool, PoolRow } from "../api";
import { cleanEventType } from "./ui";

const EVENT_STATUSES = ["进行中", "已完成", "已发行", "终止"];
const GRADES = ["S", "A", "B", "T"];

function gradeBadge(row: PoolRow) {
  if (row.event_status === "终止") {
    return <span className="badge badge-terminated">终止</span>;
  }
  const g = (row.grade || "").toUpperCase();
  if (g === "S" || g === "A" || g === "B" || g === "T") {
    return <span className={`badge badge-${g}`}>{g}</span>;
  }
  return <span className="badge badge-plain">{row.grade || "-"}</span>;
}

// 流通市值：后端可能返回「亿」或「元」，>1e6 视为元并换算为亿
function fmtMv(v: number | null): string {
  if (v == null || Number.isNaN(v)) return "-";
  const yi = v > 1e6 ? v / 1e8 : v;
  return yi.toFixed(1);
}

export default function PoolTable({ onOpen }: { onOpen: (code: string) => void }) {
  const [status, setStatus] = useState("");
  const [grade, setGrade] = useState("");
  const [q, setQ] = useState("");
  const [qApplied, setQApplied] = useState("");
  const [offset, setOffset] = useState(0);
  const [total, setTotal] = useState(0);
  const [rows, setRows] = useState<PoolRow[]>([]);
  const [err, setErr] = useState("");
  const [loading, setLoading] = useState(false);
  const limit = 50;

  useEffect(() => {
    let dead = false;
    setLoading(true);
    setErr("");
    fetchPool({ status: status || undefined, grade: grade || undefined, q: qApplied || undefined, limit, offset })
      .then((resp) => {
        if (dead) return;
        setRows(resp.rows ?? []);
        setTotal(resp.total ?? 0);
      })
      .catch((e: unknown) => {
        if (!dead) setErr(e instanceof Error ? e.message : String(e));
      })
      .finally(() => {
        if (!dead) setLoading(false);
      });
    return () => {
      dead = true;
    };
  }, [status, grade, qApplied, offset]);

  const search = () => {
    setOffset(0);
    setQApplied(q.trim());
  };

  return (
    <div>
      <div className="panel">
        <div className="toolbar">
          <select
            value={status}
            onChange={(e) => {
              setStatus(e.target.value);
              setOffset(0);
            }}
          >
            <option value="">全部流程阶段</option>
            {EVENT_STATUSES.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
          <select
            value={grade}
            onChange={(e) => {
              setGrade(e.target.value);
              setOffset(0);
            }}
          >
            <option value="">全部等级</option>
            {GRADES.map((g) => (
              <option key={g} value={g}>
                {g} 级
              </option>
            ))}
          </select>
          <input
            type="text"
            placeholder="代码 / 名称"
            style={{ width: 180 }}
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") search();
            }}
          />
          <button className="btn" onClick={search}>
            搜索
          </button>
          {loading && <span className="muted">加载中…</span>}
        </div>
      </div>

      {err && <div className="err">{err}</div>}

      <div className="panel" style={{ padding: 0, overflowX: "auto" }}>
        <table className="legacy-table">
          <thead>
            <tr>
              <th>代码</th>
              <th>名称</th>
              <th>板块</th>
              <th className="num">流通市值(亿)</th>
              <th>最新事件</th>
              <th>流程阶段</th>
              <th className="num">共振数</th>
              <th>池状态</th>
              <th>等级</th>
              <th>信号</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.code} onClick={() => onOpen(r.code)}>
                <td className="num">{r.code}</td>
                <td>
                  {r.exit_signals && r.exit_signals.length > 0 && (
                    <span title={`退出信号：${r.exit_signals.join("、")}`} style={{ marginRight: 4 }}>
                      ⚠
                    </span>
                  )}
                  {r.name}
                </td>
                <td>{r.board || "-"}</td>
                <td className="num">{fmtMv(r.circ_mv)}</td>
                <td>
                  {cleanEventType(r.event_type)}
                  <span className="muted num" style={{ marginLeft: 6 }}>
                    {r.start_date || ""}
                  </span>
                </td>
                <td>{r.event_status || "-"}</td>
                <td className="num">{r.resonance_count ?? 0}</td>
                <td>{r.status || "-"}</td>
                <td>{gradeBadge(r)}</td>
                <td>
                  {r.exit_signals && r.exit_signals.length > 0 ? (
                    <span style={{ color: "var(--yellow)" }}>⚠ {r.exit_signals.length}</span>
                  ) : (
                    <span className="muted">-</span>
                  )}
                </td>
              </tr>
            ))}
            {rows.length === 0 && !loading && !err && (
              <tr>
                <td colSpan={10} className="muted" style={{ textAlign: "center", padding: 24, cursor: "default" }}>
                  暂无数据
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      <div className="pager">
        <button className="btn" disabled={offset <= 0} onClick={() => setOffset(Math.max(0, offset - limit))}>
          上一页
        </button>
        <span className="muted num">
          {total > 0 ? offset + 1 : 0} - {Math.min(offset + limit, total)} / 共 {total} 条
        </span>
        <button
          className="btn"
          disabled={offset + limit >= total}
          onClick={() => setOffset(offset + limit)}
        >
          下一页
        </button>
      </div>
    </div>
  );
}
