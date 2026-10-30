import { useEffect, useMemo, useState } from "react";
import {
  Alert,
  Button,
  Empty,
  Input,
  Segmented,
  Select,
  Space,
  Statistic,
  Table,
  Tag,
  Tooltip,
  Typography,
} from "antd";
import { SearchOutlined } from "@ant-design/icons";
import {
  addWatchlist,
  fetchScan,
  fetchScanSummary,
  ScanRow,
  ScanSummary,
} from "../api";
import { GradeTag, HitsTags, LaneTag, MissingTags, fmtMv, fmtPct, chgColor } from "./ui";
import { useWatchCodes, refreshWatchCache } from "./watchcache";
import { RatingMutator } from "./RatingUI";
import { useRatings, refreshRatingCache } from "./ratingcache";
import { message } from "antd";

const PAGE = 100;

type LaneFilter = "all" | "新起涨" | "延续观察" | "待确认" | "无";

export default function Opportunities({ onOpen }: {
  onOpen: (code: string) => void;
}) {
  const [summary, setSummary] = useState<ScanSummary | null>(null);
  const [rows, setRows] = useState<ScanRow[]>([]);
  const [scanDate, setScanDate] = useState<string | null>(null);
  const [total, setTotal] = useState(0);
  const [err, setErr] = useState("");
  const [loading, setLoading] = useState(false);
  const [tier, setTier] = useState<string>(() => {
    try {
      return sessionStorage.getItem("zhuang.scan.tier") ?? "all";
    } catch {
      return "all";
    }
  });
  const [lane, setLane] = useState<LaneFilter>(() => {
    try {
      return (sessionStorage.getItem("zhuang.scan.lane") as LaneFilter) ?? "all";
    } catch {
      return "all";
    }
  });
  const [kw, setKw] = useState<string>(() => {
    try {
      return sessionStorage.getItem("zhuang.scan.kw") ?? "";
    } catch {
      return "";
    }
  });
  const [page, setPage] = useState(1);
  const [adding, setAdding] = useState<string | null>(null);
  const { codes } = useWatchCodes();
  const { get: getRating } = useRatings();

  useEffect(() => {
    try {
      sessionStorage.setItem("zhuang.scan.tier", tier);
      sessionStorage.setItem("zhuang.scan.lane", lane);
      sessionStorage.setItem("zhuang.scan.kw", kw);
    } catch {
      /* ignore */
    }
  }, [tier, lane, kw]);

  useEffect(() => {
    fetchScanSummary().then(setSummary).catch(() => undefined);
    setLoading(true);
    setErr("");
    fetchScan({ limit: 500 })
      .then((resp) => {
        setRows(resp.rows ?? []);
        setTotal(resp.total ?? 0);
        setScanDate(resp.date);
      })
      .catch((e: unknown) => setErr(e instanceof Error ? e.message : String(e)))
      .finally(() => setLoading(false));
  }, []);

  const filtered = useMemo(() => {
    let arr = rows;
    if (tier !== "all") arr = arr.filter((r) => r.tier === tier);
    if (lane === "新起涨" || lane === "延续观察" || lane === "待确认") {
      arr = arr.filter((r) => r.lane === lane);
    } else if (lane === "无") {
      arr = arr.filter((r) => !r.lane);
    }
    if (kw.trim()) {
      const q = kw.trim();
      arr = arr.filter((r) => r.name?.includes(q) || r.code.includes(q));
    }
    return arr;
  }, [rows, tier, lane, kw]);

  const pageRows = useMemo(() => filtered.slice((page - 1) * PAGE, page * PAGE), [filtered, page]);

  useEffect(() => {
    setPage(1);
  }, [tier, lane, kw]);

  const tierCount = (t: string) => summary?.tier_counts?.[t] ?? 0;

  const addTrack = async (r: ScanRow) => {
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
      title: "层级",
      dataIndex: "tier",
      width: 76,
      render: (t: string | null) => <GradeTag tier={t} />,
    },
    {
      title: "股票",
      key: "stock",
      width: 200,
      render: (_: unknown, r: ScanRow) => (
        <a onClick={() => onOpen(r.code)}>
          <b>{r.name || r.code}</b> <Typography.Text type="secondary">{r.code}</Typography.Text>
        </a>
      ),
    },
    { title: "板块", dataIndex: "board", width: 90, render: (v: string | null) => v || "-" },
    {
      title: "起涨路线",
      dataIndex: "lane",
      width: 92,
      render: (v: string | null) =>
        v ? (
          <LaneTag lane={v} />
        ) : (
          <Tooltip title="起涨路线由最新扫描引擎判定（新起涨≤40 交易日/延续观察 41-120/待确认）；本次扫描数据为旧引擎产出，下一轮扫描自动补标">
            <Tag style={{ marginInlineEnd: 0 }} color="default">
              待标定
            </Tag>
          </Tooltip>
        ),
    },
    {
      title: "确认起点",
      dataIndex: "confirmed_start_date",
      width: 100,
      align: "right" as const,
      render: (v: string | null, r: ScanRow) => (
        <Tooltip
          title={
            v
              ? `机械起点（最低点）${r.mechanical_start_date ?? "-"} → 确认起点 ${v}（二次上攻确认）`
              : `机械起点 ${r.mechanical_start_date ?? "-"}；未确认二次上攻`
          }
        >
          <span className="num">{v ?? r.start_date ?? "-"}</span>
        </Tooltip>
      ),
    },
    {
      title: "距今(交易日)",
      dataIndex: "start_age_days",
      width: 104,
      align: "right" as const,
      sorter: (x: ScanRow, y: ScanRow) => (x.start_age_days ?? 1e9) - (y.start_age_days ?? 1e9),
      render: (v: number | null) =>
        v == null ? (
          <span>-</span>
        ) : (
          <Tooltip title="距离确认起涨点的交易日数；越新越有价值">
            <span className="num" style={{ color: v <= 30 ? "#cf1322" : undefined, fontWeight: v <= 30 ? 600 : 400 }}>
              {v}
            </span>
          </Tooltip>
        ),
    },
    {
      title: "共振",
      dataIndex: "resonance",
      width: 70,
      align: "right" as const,
      render: (v: number) => <b className="num">{v}</b>,
    },
    {
      title: "命中项",
      dataIndex: "hits",
      width: 320,
      render: (h: string[]) => <HitsTags hits={h} />,
    },
    {
      title: "涨幅%",
      dataIndex: "gain_pct",
      width: 80,
      align: "right" as const,
      sorter: (x: ScanRow, y: ScanRow) => (x.gain_pct ?? -1e9) - (y.gain_pct ?? -1e9),
      render: (v: number | null) => <span className="num" style={{ color: chgColor(v) }}>{fmtPct(v)}</span>,
    },
    {
      title: "户数降幅%",
      dataIndex: "holder_drop_pct",
      width: 96,
      align: "right" as const,
      render: (v: number | null) => (v == null ? <span>-</span> : <span className="num">{fmtPct(v)}</span>),
    },
    {
      title: "市值(亿)",
      dataIndex: "circ_mv",
      width: 86,
      align: "right" as const,
      render: (v: number | null) => <span className="num">{fmtMv(v)}</span>,
    },
    {
      title: "数据",
      dataIndex: "missing_fields",
      width: 90,
      render: (f: string[]) => <MissingTags fields={f} />,
    },
    { title: "在池", dataIndex: "in_pool", width: 64, render: (v: boolean) => (v ? <Tag color="blue">已入池</Tag> : <Tag>未入池</Tag>) },
    {
      title: "操作",
      key: "act",
      width: 235,
      fixed: "right" as const,
      render: (_: unknown, r: ScanRow) => (
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
            <Tooltip title="加入我的跟踪">
              <Button size="small" type="text" loading={adding === r.code} onClick={() => addTrack(r)}>
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
      <div className="page-summary opportunities-summary">
        <Space size={40} wrap>
          <Statistic title="最新扫描日" value={scanDate ?? "未产出"} />
          <Statistic title="扫描宇宙(天/只)" value={summary?.universe ?? 0} />
          <Statistic title="命中总数" value={total} />
          <Statistic title="S 级" value={tierCount("S")} valueStyle={{ color: "#cf1322" }} />
          <Statistic title="A 级" value={tierCount("A")} valueStyle={{ color: "#fa8c16" }} />
          <Statistic title="B 级" value={tierCount("B")} valueStyle={{ color: "#1677ff" }} />
          <Statistic title="观察" value={tierCount("观察")} valueStyle={{ color: "#8c8c8c" }} />
        </Space>
        <div style={{ marginTop: 10 }}>
          <Typography.Text type="secondary">
            全部标的按 层级 → 共振 → 涨幅 排序；「新起涨」≤40 个交易日，「延续观察」41–120。距今天数 ≤30 的标红色。
            {total > 500 && " 已加载前 500 条；更早结果需等待下一扫描窗口。"}
          </Typography.Text>
        </div>
      </div>

      {err && <Alert type="error" showIcon message={err} />}
      {total > 0 && (
        <Alert className="opportunities-help"
          type="info"
          showIcon
          message={
            <span>
              “新起涨”时间越新参与价值越大；“户数”显示百分比为负=散户减少筹码集中（利好）。
              数据不足只标“未知”，不会被错误显示为 0。
            </span>
          }
        />
      )}

      <div className="filter-panel opportunities-table-panel">
        <Space wrap size={8} style={{ marginBottom: 12 }}>
          <Segmented
            value={lane}
            onChange={(v) => setLane(v as LaneFilter)}
            options={[
              { label: "全部", value: "all" },
              { label: "新起涨", value: "新起涨" },
              { label: "延续观察", value: "延续观察" },
              { label: "待确认", value: "待确认" },
              { label: "未标定", value: "无" },
            ]}
          />
          <Select
            value={tier}
            style={{ width: 100 }}
            onChange={setTier}
            options={[
              { value: "all", label: "全部层级" },
              { value: "S", label: "S 级" },
              { value: "A", label: "A 级" },
              { value: "B", label: "B 级" },
              { value: "观察", label: "观察" },
              { value: "待确认", label: "待确认" },
              { value: "延续观察", label: "延续观察" },
            ]}
          />
          <Input
            value={kw}
            onChange={(e) => setKw(e.target.value)}
            placeholder="按名称 / 代码过滤"
            allowClear
            prefix={<SearchOutlined />}
            style={{ width: 180 }}
          />
          <Typography.Text type="secondary" style={{ marginLeft: "auto" }}>
            符合当前条件：<b className="num">{filtered.length}</b> / {total}
          </Typography.Text>
        </Space>

        <Table<ScanRow>
          rowKey="code"
          columns={columns}
          dataSource={pageRows}
          loading={loading}
          size="middle"
          className="opportunities-table"
          tableLayout="fixed"
          scroll={{ x: 1500 }}
          onRow={(r) => ({
            onClick: () => onOpen(r.code),
            style: { cursor: "pointer" },
          })}
          pagination={{
            current: page,
            pageSize: PAGE,
            total: filtered.length,
            showTotal: (t) => `共 ${t} 条`,
            onChange: setPage,
          }}
          locale={{
            emptyText: !loading ? (
              <Empty
                image={Empty.PRESENTED_IMAGE_SIMPLE}
                description={
                  lane !== "all"
                    ? "本路线暂无标的：最新一次扫描数据未标定路线（数据升级前产出），等待下一交易日扫描后自动补全；可切换到「未标定」查看现有标的"
                    : "符合条件的标的为 0：可放宽筛选"
                }
              />
            ) : undefined,
          }}
        />
      </div>
    </Space>
  );
}
