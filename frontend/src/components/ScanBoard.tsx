import { useEffect, useState } from "react";
import { fetchScan, fetchScanSummary, ScanRow, ScanSummary } from "../api";

const PAGE = 100;

function fmtPct(v: number | null, digits = 1): string {
  if (v == null || Number.isNaN(v)) return "-";
  return v.toFixed(digits);
}

// 流通市值：后端可能返回「亿」或「元」，>1e6 视为元并换算为亿（与候选池页口径一致）
function fmtMv(v: number | null): string {
  if (v == null || Number.isNaN(v)) return "-";
  const yi = v > 1e6 ? v / 1e8 : v;
  return yi.toFixed(1);
}

function tierBadge(tier: string | null) {
  if (tier === "A") return <span className="badge badge-A">A</span>;
  if (tier === "观察") return <span className="badge badge-plain">观察</span>;
  return <span className="muted">{tier || "-"}</span>;
}

export default function ScanBoard({ onOpen }: { onOpen: (code: string) => void }) {
  const [summary, setSummary] = useState<ScanSummary | null>(null);
  const [rows, setRows] = useState<ScanRow[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [err, setErr] = useState("");
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    fetchScanSummary().then(setSummary).catch(() => undefined);
  }, []);

  useEffect(() => {
    let dead = false;
    setLoading(true);
    setErr("");
    fetchScan({ limit: PAGE, offset })
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
  }, [offset]);

  const tierCounts = summary ? Object.entries(summary.tier_counts || {}) : [];

  return (
    <div>
      <div className="cards">
        <div className="stat-card">
          <div className="k">最新扫描日</div>
          <div>{summary?.latest_date || "尚未产出"}</div>
        </div>
        <div className="stat-card">
          <div className="k">扫描宇宙</div>
          <div className="num">{summary?.universe ?? 0}</div>
        </div>
        <div className="stat-card">
          <div className="k">命中条数</div>
          <div className="num">{summary?.hits ?? 0}</div>
        </div>
        <div className="stat-card">
          <div className="k">层级分布</div>
          <div>
            {tierCounts.length === 0 && <span className="muted">-</span>}
            {tierCounts.map(([t, n]) => (
              <span key={t} style={{ marginRight: 10 }}>
                {t} <span className="num">{n}</span>
              </span>
            ))}
          </div>
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
              <th className="num">涨幅%</th>
              <th className="num">户数降幅%</th>
              <th className="num">流通市值(亿)</th>
              <th className="num">共振数</th>
              <th>层级</th>
              <th>命中项</th>
              <th>在池</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.code} onClick={() => onOpen(r.code)}>
                <td className="num">{r.code}</td>
                <td>{r.name || "?"}</td>
                <td>{r.board || "-"}</td>
                <td className="num">{fmtPct(r.gain_pct)}</td>
                <td className="num">{fmtPct(r.holder_drop_pct)}</td>
                <td className="num">{fmtMv(r.circ_mv)}</td>
                <td className="num">{r.resonance ?? 0}</td>
                <td>{tierBadge(r.tier)}</td>
                <td>{(r.hits ?? []).join("、") || "-"}</td>
                <td>{r.in_pool ? <span style={{ color: "var(--orange)" }}>●</span> : <span className="muted">-</span>}</td>
              </tr>
            ))}
            {rows.length === 0 && !loading && !err && (
              <tr>
                <td colSpan={10} className="muted" style={{ textAlign: "center", padding: 24, cursor: "default" }}>
                  榜单尚未产出：等待全市场 K 线回填完成后运行因子扫描
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      <div className="pager">
        <button className="btn" disabled={offset <= 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>
          上一页
        </button>
        <span className="muted num">
          {total > 0 ? offset + 1 : 0} - {Math.min(offset + PAGE, total)} / 共 {total} 条
        </span>
        <button className="btn" disabled={offset + PAGE >= total} onClick={() => setOffset(offset + PAGE)}>
          下一页
        </button>
      </div>
    </div>
  );
}
