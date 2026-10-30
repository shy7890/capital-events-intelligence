import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Alert,
  Button,
  Collapse,
  Empty,
  Input,
  message,
  Select,
  Space,
  Statistic,
  Table,
  Tag,
  Tooltip,
  Typography,
} from "antd";
import {
  PlusOutlined,
  ReloadOutlined,
  WarningOutlined,
  UndoOutlined,
} from "@ant-design/icons";
import { addWatchlist, fetchPool, fetchDailyReport, fetchEvents, PoolRow, DailyReport } from "../api";
import { cleanEventType, GradeTag, StatusTag, TerminatedTag, fmtMv, fmtDate } from "./ui";
import { useWatchCodes, refreshWatchCache } from "./watchcache";
import { RatingTag, RatingMutator } from "./RatingUI";
import { useRatings, refreshRatingCache } from "./ratingcache";

const POOL_STATUSES = ["新入池", "观察中", "重点跟踪", "催化兑现中", "已出信号", "离场"];
const EVENT_STATUSES = ["进行中", "已完成", "已发行", "终止", "未公布"];
const GRADES = ["S", "A"];
const EVENT_KINDS = ["重组", "定增", "控制权"];

/** 最早事件缓存：code → { event_type, start_date }（module 级，比页面活得久） */
const firstEventCache = new Map<string, { event_type: string; start_date: string }>();
let firstEventQueue: string[] = [];
let firstEventRunning = false;
const firstEventDone: Array<() => void> = [];

async function pumpFirstEvents(limit = 5) {
  if (firstEventRunning) return;
  firstEventRunning = true;
  try {
    while (firstEventQueue.length > 0) {
      const batch = firstEventQueue.splice(0, limit);
      if (batch.length === 0) break;
      await Promise.all(
        batch
          .filter((c) => !firstEventCache.has(c))
          .map(async (c) => {
            try {
              const r = await fetchEvents(c);
              const evs = r.events ?? [];
              if (evs.length > 0) {
                const first = evs.reduce((a, b) => (a.start_date <= b.start_date ? a : b));
                firstEventCache.set(c, { event_type: first.event_type || "", start_date: first.start_date || "" });
              } else {
                firstEventCache.set(c, { event_type: "", start_date: "" });
              }
            } catch {
              firstEventCache.set(c, { event_type: "", start_date: "" });
            }
          }),
      );
    }
  } finally {
    firstEventRunning = false;
    const done = firstEventDone.splice(0);
    done.forEach((fn) => fn());
  }
}

function queueFirstEvents(codes: string[]) {
  for (const c of codes) {
    if (!firstEventCache.has(c)) firstEventQueue.push(c);
  }
  void pumpFirstEvents();
}

function useFirstEvents(): { get: (code: string) => { event_type: string; start_date: string } | null; bump: number } {
  const [bump, setBump] = useState(0);
  useEffect(() => {
    const fn = () => setBump((b) => b + 1);
    firstEventDone.push(fn);
    // 侦测是否已有运行中的队列并在完成时刷新
    if (!firstEventRunning && firstEventQueue.length > 0) void pumpFirstEvents();
    return () => {
      const i = firstEventDone.indexOf(fn);
      if (i >= 0) firstEventDone.splice(i, 1);
    };
  }, []);
  return {
    get: (code: string) => firstEventCache.get(code) ?? null,
    bump,
  };
}

interface Props {
  onOpen: (code: string) => void;
}

type ReviewedFilter = "all" | "reviewed" | "unreviewed";

function usePoolQuery(init?: Partial<{ status: string; grade: string; q: string; eventK: string; reviewed: ReviewedFilter; hideReviewed: boolean; page: number; pageSize: number }>) {
  const saved = useMemo(() => {
    try {
      const raw = sessionStorage.getItem("zhuang.today.flt");
      if (raw) {
        const v = JSON.parse(raw) as { status?: string; grade?: string; q?: string; eventK?: string; reviewed?: ReviewedFilter; hideReviewed?: boolean; page?: number; pageSize?: number };
        // 消费即删：避免后续读取时与挂载写入竞态（挂载期 effect 会写回初始值）
        sessionStorage.removeItem("zhuang.today.flt");
        return v;
      }
    } catch {
      /* ignore */
    }
    return init ?? {};
  }, []);
  const [status, setStatus] = useState(saved.status ?? "");
  const [grade, setGrade] = useState(saved.grade ?? "");
  const [input, setInput] = useState(saved.q ?? "");
  const [q, setQ] = useState(saved.q ?? "");
  const [eventK, setEventK] = useState(saved.eventK ?? "");
  const [reviewed, setReviewed] = useState<ReviewedFilter>(
    saved.reviewed === "reviewed" || saved.reviewed === "unreviewed"
      ? saved.reviewed
      : saved.hideReviewed
        ? "unreviewed"
        : "all",
  );
  const [page, setPage] = useState(saved.page ?? 1);
  const [pageSize, setPageSize] = useState(saved.pageSize ?? 50);

  // 用户交互后备份（返回来源页时恢复筛选/页码）；首次挂载不写回（等用户真正改动）
  const bgFromSaved = useRef(saved.page != null && (saved.status != null || saved.grade != null || saved.q != null || saved.reviewed != null || saved.hideReviewed != null || saved.page != 1));
  const lastSave = useRef("");
  useEffect(() => {
    const snapshot = JSON.stringify({ status, grade, q, eventK, reviewed, page, pageSize });
    if (snapshot === lastSave.current) return;
    if (!bgFromSaved.current) {
      // 首次跳过：直接从恢复值开始，避免把初始 page=1 写回
      bgFromSaved.current = true;
    }
    lastSave.current = snapshot;
    try {
      sessionStorage.setItem("zhuang.today.flt", snapshot);
    } catch {
      /* ignore */
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status, grade, q, eventK, reviewed, page, pageSize]);

  return { status, setStatus, grade, setGrade, input, setInput, q, setQ, eventK, setEventK, reviewed, setReviewed, page, setPage, pageSize, setPageSize };
}

function GradeStat({ label, value, color, desc }: { label: string; value: number; color: string; desc: string }) {
  return (
    <Tooltip title={desc}>
      <Statistic title={label} value={value} valueStyle={{ color }} />
    </Tooltip>
  );
}

/** 今日运行状态 → 一串人话；失败才显眼，正常低调。 */
function TodayStatus({ report }: { report: DailyReport | null }) {
  if (!report) return null;
  const mr = report.market_refresh;

  const parts: string[] = [];
  if (mr) {
    if (mr.kline_failed > 0) parts.push(`K线失败 ${mr.kline_failed} 只`);
    else if (mr.kline_pending > 0) parts.push(`K线进行中（待完成 ${mr.kline_pending}）`);
    else if (mr.kline_total > 0) parts.push(`K线更新完成`);
    else parts.push("K线未运行");

    const lh = mr.longhu_status;
    if (lh === "failed" || lh === "error") parts.push("龙虎榜失败");
    else if (lh === "pending" || lh === "running") parts.push("龙虎榜进行中");
    else if (lh === "done" || lh === "ok") parts.push("龙虎榜更新完成");
    else parts.push("龙虎榜未运行");
  }

  const stages = report.today_stages ?? [];
  const failed = stages.filter((s) => (s.status || "").toLowerCase() === "red" || (s.status || "").toLowerCase() === "error");
  const other = stages.filter((s) => !failed.includes(s));

  const hasBad = (mr?.kline_failed ?? 0) > 0 || failed.length > 0 || (mr?.longhu_status ?? "") === "failed";
  if (!hasBad) {
    return (
      <Alert
        type="info"
        showIcon
        message={
          <Space size={4} wrap>
            {mr && (mr.kline_total ?? 0) === 0 && stages.length === 0 ? (
              <span>
                今日数据尚未开始更新：晚间批任务会在 <b>17:30</b> 自动开始（21:30 补跑），
                现在看到的是上一个交易日的完整数据
              </span>
            ) : (
              <>
                <span>今日数据：</span>
                {parts.map((p, i) => (
                  <Tag key={i} style={{ marginInlineEnd: 0 }}>
                    {p}
                  </Tag>
                ))}
                {other.map((s, i) => (
                  <Tag key={`s${i}`} style={{ marginInlineEnd: 0 }}>
                    {s.stage} 已跑
                  </Tag>
                ))}
              </>
            )}
          </Space>
        }
      />
    );
  }
  const badParts: string[] = [];
  if ((mr?.kline_failed ?? 0) > 0) badParts.push(`K线采集失败 ${mr.kline_failed} 只，榜单可能延迟`);
  if (failed.length > 0) badParts.push(`${failed.map((f) => f.stage).join("、")} 运行失败`);
  if ((mr?.longhu_status ?? "") === "failed") badParts.push("龙虎榜采集失败");
  return (
    <Alert
      type="error"
      showIcon
      message="今天的自动更新部分失败"
      description={
        <Space direction="vertical" size={2}>
          {badParts.map((b, i) => (
            <span key={i}>{b}</span>
          ))}
          <Typography.Text type="secondary">可以点击右上角「运行与提醒」查看详情。</Typography.Text>
        </Space>
      }
    />
  );
}

export default function Today({ onOpen }: Props) {
  const [rows, setRows] = useState<PoolRow[]>([]);
  const [allRows, setAllRows] = useState<PoolRow[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState("");
  const [report, setReport] = useState<DailyReport | null>(null);
  const [archived, setArchived] = useState<PoolRow[] | null>(null);
  const [archivedLoading, setArchivedLoading] = useState(false);
  const [archivedTotal, setArchivedTotal] = useState(0);
  const [archivePage, setArchivePage] = useState(1);
  const [adding, setAdding] = useState<string | null>(null);
  const { codes } = useWatchCodes();
  const flt = usePoolQuery();
  const { get: getFirstEv } = useFirstEvents();
  const { get: getRating } = useRatings();
  // 竞态保护：只应用最后一次请求的结果（筛选/翻页快速连续操作时不出现旧数据覆盖新数据）
  const seqRef = useRef(0);

  const load = useCallback(
    (page: number, pageSize: number) => {
      const seq = ++seqRef.current;
      setLoading(true);
      setErr("");
      fetchPool({
        status: flt.status || undefined,
        grade: flt.grade || undefined,
        q: flt.q || undefined,
        event_kind: flt.eventK || undefined,
        reviewed: flt.reviewed === "all" ? undefined : flt.reviewed,
        limit: pageSize,
        offset: (page - 1) * pageSize,
      })
        .then((r) => {
          if (seq !== seqRef.current) return;
          setRows(r.rows ?? []);
          setTotal(r.total ?? 0);
          // 拉取当前行的“最初事件”信息（批、并发受限）
          queueFirstEvents((r.rows ?? []).map((x) => x.code));
        })
        .catch((e: unknown) => {
          if (seq !== seqRef.current) return;
          setErr(e instanceof Error ? e.message : String(e));
        })
        .finally(() => {
          if (seq === seqRef.current) setLoading(false);
        });
    },
    [flt.status, flt.grade, flt.q, flt.eventK, flt.reviewed],
  );

  // 首屏：加载恢复的筛选/页码 + 运行报告 + 全量统计（只跑一次）
  useEffect(() => {
    load(flt.page, flt.pageSize);
    fetchDailyReport()
      .then(setReport)
      .catch(() => undefined);
    // 全量统计：数据量 600+，一次拉完避免分页统计偏差
    fetchPool({ limit: 500 })
      .then((r) => setAllRows(r.rows ?? []))
      .catch(() => undefined)
      .finally(() => {
        fetchPool({ limit: 500, offset: 500 })
          .then((r) => {
            const s = r.rows ?? [];
            setAllRows((prev) => {
              const seen = new Set((prev ?? []).map((x) => x.code));
              return [...(prev ?? []), ...s.filter((x) => !seen.has(x.code))];
            });
          })
          .catch(() => undefined);
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 筛选变化 → 回到第 1 页
  useEffect(() => {
    flt.setPage(1);
    // 防抖：连续切换时不重复 setPage，由 load 统一生效
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [flt.status, flt.grade, flt.q, flt.eventK, flt.reviewed]);

  // 页码/筛选/每页条数变化 → 加载（含恢复后首次，因为 flt.page 初始即恢复值）
  useEffect(() => {
    load(flt.page, flt.pageSize);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [flt.page, flt.pageSize, flt.status, flt.grade, flt.q, flt.eventK, flt.reviewed]);

  const hasFlt = Boolean(flt.status || flt.grade || flt.q || flt.eventK || flt.reviewed !== "all");

  const stats = useMemo(() => {
    let s = 0;
    let a = 0;
    let signal = 0;
    let hot = 0;
    for (const r of allRows) {
      if (r.grade === "S") s++;
      if (r.grade === "A") a++;
      if (r.exit_signals?.length) signal++;
      if (r.resonance_count != null && r.resonance_count >= 4) hot++;
    }
    return { s, a, signal, hot };
  }, [allRows]);

  const loadArchived = (p: number) => {
    setArchivedLoading(true);
    fetchPool({ status: "归档", include_archived: true, limit: 50, offset: (p - 1) * 50 })
      .then((r) => {
        setArchived(r.rows ?? []);
        setArchivedTotal(r.total ?? 0);
      })
      .catch(() => {
        setArchived([]);
        setArchivedTotal(0);
      })
      .finally(() => setArchivedLoading(false));
  };

  const addTrack = async (r: PoolRow) => {
    setAdding(r.code);
    try {
      await addWatchlist(r.code);
      refreshWatchCache();
      message.success(`已加入跟踪：${r.name || r.code}`);
    } catch (e: unknown) {
      message.error(e instanceof Error ? e.message : String(e));
    } finally {
      setAdding(null);
    }
  };

  const columns = [
    {
      title: "等级",
      dataIndex: "grade",
      width: 64,
      render: (_: string, r: PoolRow) => (r.event_status === "终止" ? <TerminatedTag status={r.event_status} /> : <GradeTag grade={r.grade} />),
    },
    {
      title: "股票",
      key: "stock",
      width: 190,
      render: (_: unknown, r: PoolRow) => (
        <a onClick={() => onOpen(r.code)} style={{ whiteSpace: "nowrap" }}>
          <b>{r.name || "-"}</b> <Typography.Text type="secondary">{r.code}</Typography.Text>
        </a>
      ),
    },
    { title: "板块", dataIndex: "board", width: 90, render: (v: string) => v || "-" },
    {
      title: "流通市值(亿)",
      dataIndex: "circ_mv",
      width: 100,
      align: "right" as const,
      sorter: (x: PoolRow, y: PoolRow) => (x.circ_mv ?? -1) - (y.circ_mv ?? -1),
      render: (v: number | null) => <span className="num">{fmtMv(v)}</span>,
    },
    {
      title: "最新事件",
      key: "event",
      width: 200,
      render: (_: unknown, r: PoolRow) => (
        <Tooltip title={`最新事件：${cleanEventType(r.event_type)}（${fmtDate(r.start_date)}）`}>
          <span>{cleanEventType(r.event_type)}</span>
          <Typography.Text type="secondary" style={{ marginLeft: 6 }} className="num">
            {fmtDate(r.start_date)}
          </Typography.Text>
        </Tooltip>
      ),
    },
    {
      title: "最初事件",
      key: "firstEvent",
      width: 200,
      render: (_: unknown, r: PoolRow) => {
        const fe = getFirstEv(r.code);
        return (
          <Tooltip title={fe && fe.start_date ? `最早一次资本运作：${cleanEventType(fe.event_type)}（${fe.start_date}）` : "事件信息加载中…"}>
            <span>
              {fe && fe.event_type ? (
                <>
                  {cleanEventType(fe.event_type)}
                  <Typography.Text type="secondary" className="num" style={{ marginLeft: 6 }}>
                    {fe.start_date || "-"}
                  </Typography.Text>
                </>
              ) : (
                <Typography.Text type="secondary">…</Typography.Text>
              )}
            </span>
          </Tooltip>
        );
      },
    },
    {
      title: "看过",
      key: "rating",
      width: 90,
      render: (_: unknown, r: PoolRow) => {
        if (getRating(r.code)) return <RatingTag rating={getRating(r.code)?.rating} />;
        return (
          <Typography.Text type="secondary" style={{ fontSize: 11 }}>
            未看
          </Typography.Text>
        );
      },
    },
    { title: "流程阶段", dataIndex: "event_status", width: 90, render: (v: string) => v || "-" },
    {
      title: "共振数",
      dataIndex: "resonance_count",
      width: 80,
      align: "right" as const,
      sorter: (x: PoolRow, y: PoolRow) => (x.resonance_count ?? -1) - (y.resonance_count ?? -1),
      render: (v: number | null) =>
        v == null ? <span>-</span> : <b className="num" style={{ color: v >= 4 ? "#cf1322" : undefined }}>{v}</b>,
    },
    { title: "池状态", dataIndex: "status", width: 110, render: (v: string) => <StatusTag status={v} /> },
    {
      title: "退出信号",
      dataIndex: "exit_signals",
      width: 130,
      render: (sig: string[]) =>
        sig && sig.length > 0 ? (
          <Tooltip title={sig.join("、")}>
            <Tag color="warning" style={{ marginInlineEnd: 0 }}>
              <WarningOutlined aria-hidden="true" /> {sig.length} 项：{sig[0]}
              {sig.length > 1 ? "…" : ""}
            </Tag>
          </Tooltip>
        ) : (
          <Typography.Text type="secondary">-</Typography.Text>
        ),
    },
    {
      title: "操作",
      key: "act",
      width: 250,
      fixed: "right" as const,
      render: (_: unknown, r: PoolRow) => (
        <Space
          size={4}
          onClick={(e) => {
            e.stopPropagation();
          }}
        >
          <RatingMutator code={r.code} current={getRating(r.code)?.rating ?? null} onChanged={() => refreshRatingCache()} />
          {codes.has(r.code) ? (
            <Tag color="blue" style={{ marginInlineEnd: 0 }}>
              已跟踪
            </Tag>
          ) : (
            <Tooltip title="加入我的跟踪，跟踪后可实时收到该股重要变化提醒">
              <Button
                size="small"
                type="text"
                icon={<PlusOutlined />}
                loading={adding === r.code}
                onClick={() => addTrack(r)}
              >
                跟踪
              </Button>
            </Tooltip>
          )}
        </Space>
      ),
    },
  ];

  return (
    <Space direction="vertical" size={14} style={{ display: "flex" }}>
      <TodayStatus report={report} />

      <div
        className="page-summary today-summary"
        style={{
          background: "#fff",
          borderRadius: 8,
          padding: "16px 20px",
          border: "1px solid #eaeef2",
        }}
      >
        <Space size={40} wrap>
          <GradeStat label="重点等级" value={stats.s + stats.a} color="#9f1239" desc={`当前全池 S/A 级数量（S ${stats.s} · A ${stats.a}）`} />
          <GradeStat label="强共振" value={stats.hot} color="#1d5fa7" desc="多信号共振更强的候选" />
          <GradeStat
            label="需处理"
            value={stats.signal}
            color="#b54708"
            desc="可能已错过阶段，建议注意风险"
          />
          <GradeStat label="当前结果" value={total} color="#172033" desc="当前筛选条件的候选总数" />
        </Space>
        <Typography.Text type="secondary" className="summary-footnote">池内总数 {total} · S {stats.s} · A {stats.a}</Typography.Text>
      </div>

      <div className="filter-panel">
        <Space wrap size={8} style={{ marginBottom: 12 }}>
          <Select
            value={flt.grade || undefined}
            placeholder="等级"
            allowClear
            style={{ width: 110 }}
            options={GRADES.map((g) => ({ value: g, label: `${g} 级` }))}
            onChange={(v) => flt.setGrade(v ?? "")}
          />
          <Select
            value={flt.status || undefined}
            placeholder="池状态"
            allowClear
            style={{ width: 130 }}
            options={POOL_STATUSES.map((s) => ({ value: s, label: s }))}
            onChange={(v) => flt.setStatus(v ?? "")}
          />
          <Select
            value={flt.eventK || undefined}
            placeholder="事件属性"
            allowClear
            style={{ width: 130 }}
            options={EVENT_KINDS.map((k) => ({ value: k, label: `重大${k}` === "重大重组" ? "重大重组" : k === "定增" ? "定增" : "控制权变更" }))}
            onChange={(v) => flt.setEventK(v ?? "")}
          />
          <Select
            value={flt.reviewed}
            aria-label="看过状态"
            style={{ width: 140 }}
            options={[
              { value: "all", label: "看过状态：全部" },
              { value: "unreviewed", label: "只看未看过" },
              { value: "reviewed", label: "只看已看过" },
            ]}
            onChange={(v: ReviewedFilter) => flt.setReviewed(v)}
          />
          <Input
            value={flt.input}
            onChange={(e) => flt.setInput(e.target.value)}
            onPressEnter={() => {
              flt.setQ(flt.input.trim());
            }}
            placeholder="输入代码或名称，回车搜索"
            style={{ width: 220 }}
            allowClear
            onClear={() => {
              flt.setQ("");
            }}
          />
          <Button type="primary" onClick={() => flt.setQ(flt.input.trim())}>
            搜索
          </Button>
          {hasFlt ? (
            <Button
              icon={<UndoOutlined />}
              onClick={() => {
                flt.setStatus("");
                flt.setGrade("");
                flt.setEventK("");
                flt.setReviewed("all");
                flt.setInput("");
                flt.setQ("");
              }}
            >
              清除筛选
            </Button>
          ) : (
            <Button icon={<ReloadOutlined />} onClick={() => { refreshRatingCache(); load(flt.page, flt.pageSize); }}>
              刷新
            </Button>
          )}
          {hasFlt && (
            <Typography.Text type="secondary">
              正在查看：{[
                flt.grade && `等级 ${flt.grade}`,
                flt.status && `池状态 ${flt.status}`,
                flt.eventK && `事件 ${flt.eventK === "重组" ? "重大重组" : flt.eventK === "定增" ? "定增" : "控制权变更"}`,
                flt.q && `搜索「${flt.q}」`,
                flt.reviewed === "reviewed" && "已看过",
                flt.reviewed === "unreviewed" && "未看过",
              ]
                .filter(Boolean)
                .join(" · ")}
            </Typography.Text>
          )}
        </Space>

        <Table<PoolRow>
          rowKey="code"
          columns={columns}
          dataSource={rows}
          loading={loading}
          size="middle"
          className="pool-table"
          tableLayout="fixed"
          scroll={{ x: 1610 }}
          onRow={(r) => ({
            onClick: () => onOpen(r.code),
            style: { cursor: "pointer" },
          })}
          pagination={{
            current: flt.page,
            pageSize: flt.pageSize,
            total,
            showSizeChanger: true,
            pageSizeOptions: [20, 50, 100],
            showTotal: (t) => `共 ${t} 条`,
            onChange: (p, ps) => {
              flt.setPageSize(ps);
              flt.setPage(p);
            },
          }}
          locale={{
            emptyText: (
              <Empty
                image={Empty.PRESENTED_IMAGE_SIMPLE}
                description={
                  err ? (
                    <Typography.Text type="danger">{err}</Typography.Text>
                  ) : (
                    flt.q ? <>没有找到「{flt.q}」相关的候选</> : <>暂无候选：等待晚间数据更新</>
                  )
                }
              />
            ),
          }}
        />
      </div>

      <Collapse
        onChange={(keys) => {
          if ((keys as string[]).includes("archived")) {
            if (archived === null && !archivedLoading) loadArchived(1);
            else if (archived !== null && archivedTotal === 0) loadArchived(archivePage);
          }
        }}
        items={[
          {
            key: "archived",
            label: (
              <span>
                历史复盘（已归档 {archivedTotal > 0 ? `共 ${archivedTotal} 只` : "…"}）
                <Typography.Text type="secondary" style={{ marginLeft: 8, fontWeight: 400 }}>
                  终态/僵尸/超限事件自动归档，不参与新机会，但保留复盘价值；展开自动加载前 50 只
                </Typography.Text>
              </span>
            ),
            children: (
          <Table<PoolRow>
            rowKey="code"
            size="small"
            loading={archivedLoading}
            dataSource={archived ?? []}
            columns={[
              {
                title: "股票",
                key: "stock",
                width: 190,
                render: (_: unknown, r: PoolRow) => (
                  <a onClick={() => onOpen(r.code)}>
                    <b>{r.name || "-"}</b> <Typography.Text type="secondary">{r.code}</Typography.Text>
                  </a>
                ),
              },
              { title: "板块", dataIndex: "board", width: 90 },
              { title: "市值(亿)", dataIndex: "circ_mv", width: 90, align: "right" as const, render: (v: number | null) => <span className="num">{fmtMv(v)}</span> },
              { title: "最新事件", key: "ev", width: 200, render: (_: unknown, r: PoolRow) => (
                <span>{r.event_type || "-"} <span className="num mutedText">{fmtDate(r.start_date)}</span></span>
              ) },
              { title: "流程", dataIndex: "event_status", width: 80 },
              { title: "状态", dataIndex: "status", width: 90, render: (v: string) => <StatusTag status={v} /> },
              { title: "退出信号", key: "sig", width: 200, render: (_: unknown, r: PoolRow) => (
                r.exit_signals?.length ? <Tag color="warning">{r.exit_signals.join("、")}</Tag> : <Typography.Text type="secondary">-</Typography.Text>
              ) },
            ]}
            pagination={{
              current: archivePage,
              pageSize: 50,
              total: archivedTotal,
              onChange: (p) => {
                setArchivePage(p);
                loadArchived(p);
              },
            }}
          />
        ),
      },
    ]}
  />
</Space>
);
}
