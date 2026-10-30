import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Alert,
  Button,
  Card,
  Descriptions,
  Empty,
  Input,
  message,
  Popconfirm,
  Popover,
  Space,
  Table,
  Tag,
  Timeline,
  Tooltip,
  Typography,
} from "antd";
import {
  ArrowRightOutlined,
  DeleteOutlined,
  EditOutlined,
  EyeOutlined,
  PlusOutlined,
  ReloadOutlined,
} from "@ant-design/icons";
import {
  addWatchlist,
  fetchWatchlist,
  updateWatchlist,
  removeWatchlist,
  WatchlistRow,
  WatchlistChange,
} from "../api";
import { LineChartOutlined } from "@ant-design/icons";
import { refreshWatchCache } from "./watchcache";
import { GradeTag, LaneTag, MissingTags, fmtDate, fmtDt, chgColor } from "./ui";
import { RatingMutator } from "./RatingUI";
import { useRatings, refreshRatingCache, RatingEntry } from "./ratingcache";

interface Props {
  onOpen: (code: string) => void;
}

function todayLocal(): string {
  const d = new Date();
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

const CHANGE_ICON: Record<string, { icon: React.ReactNode; color: string }> = {
  event: {
    icon: <ArrowRightOutlined />,
    color: "#fa8c16",
  },
  node: {
    icon: <ArrowRightOutlined />,
    color: "#1677ff",
  },
  missing_data: {
    icon: <LineChartOutlined />,
    color: "#d4b106",
  },
  exit_signal: {
    icon: <LineChartOutlined />,
    color: "#cf1322",
  },
  move_up: {
    icon: <LineChartOutlined />,
    color: "#cf1322",
  },
};

function ChangeLine({ c }: { c: WatchlistChange }) {
  const meta = CHANGE_ICON[c.type] ?? CHANGE_ICON["node"];
  return (
    <div style={{ marginBottom: 6 }}>
      <Tag color={meta.color} style={{ marginInlineEnd: 6, marginBottom: 4 }}>
        {c.title}
      </Tag>
      <Typography.Text type="secondary" className="num" style={{ whiteSpace: "nowrap" }}>
        {c.date}
      </Typography.Text>
      <div style={{ marginTop: 2 }}>{c.detail}</div>
    </div>
  );
}

function FreshnessText({ r }: { r: WatchlistRow }) {
  const f = r.freshness;
  if (!f) return <span>-</span>;
  const items: string[] = [];
  if (f.kline) items.push(`K线 ${f.kline.slice(5)}`);
  if (f.holder) items.push(`户数 ${f.holder.slice(5)}`);
  if (f.longhu) items.push(`龙虎榜 ${f.longhu.slice(5)}`);
  if (f.announcement) items.push(`公告 ${f.announcement.slice(5)}`);
  if (items.length === 0) return <Typography.Text type="secondary">尚未更新</Typography.Text>;
  return (
    <Tooltip title={items.join("\n")}>
      <span style={{ whiteSpace: "nowrap" }}>{items.join(" · ")}</span>
    </Tooltip>
  );
}

export default function Watchlist({ onOpen }: Props) {
  const [active, setActive] = useState<WatchlistRow[]>([]);
  const [history, setHistory] = useState<WatchlistRow[]>([]);
  const [observe, setObserve] = useState<RatingEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState("");
  const [view, setView] = useState<"active" | "observe" | "history">("active");
  const [addCode, setAddCode] = useState("");
  const [adding, setAdding] = useState(false);
  const [noteEditing, setNoteEditing] = useState<string | null>(null);
  const [noteDraft, setNoteDraft] = useState("");
  const { get: getRating } = useRatings();

  const load = useCallback(() => {
    setLoading(true);
    setErr("");
    Promise.all([
      fetchWatchlist(false, 500),
      fetchWatchlist(true, 500),
      fetch("/api/ratings?limit=500").then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status))))),
    ])
      .then(([a, h, r]) => {
        setActive(a.rows ?? []);
        setHistory((h.rows ?? []).filter((x) => x.status === "history"));
        setObserve(
          ((r as { rows?: RatingEntry[] }).rows ?? []).filter(
            (x) => x.rating === "medium" || x.rating === "low",
          ),
        );
      })
      .catch((e: unknown) => setErr(e instanceof Error ? e.message : String(e)))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const add = async () => {
    const c = addCode.trim();
    if (!/^\d{6}$/.test(c)) {
      message.warning("请输入正确的 6 位股票代码");
      return;
    }
    setAdding(true);
    try {
      await addWatchlist(c);
      refreshWatchCache();
      message.success("已加入跟踪");
      setAddCode("");
      load();
    } catch (e: unknown) {
      message.error(e instanceof Error ? e.message : String(e));
    } finally {
      setAdding(false);
    }
  };

  const saveNote = async (r: WatchlistRow) => {
    try {
      await updateWatchlist(r.code, { note: noteDraft.trim() || undefined });
      message.success("备注已保存");
      setNoteEditing(null);
      load();
    } catch (e: unknown) {
      message.error(e instanceof Error ? e.message : String(e));
    }
  };

  const toHistory = async (r: WatchlistRow) => {
    try {
      await removeWatchlist(r.code);
      refreshWatchCache();
      message.success(`已移入历史：${r.name || r.code}（可随时恢复）`);
      load();
    } catch (e: unknown) {
      message.error(e instanceof Error ? e.message : String(e));
    }
  };

  const restore = async (r: WatchlistRow) => {
    try {
      await updateWatchlist(r.code, { status: "active" });
      refreshWatchCache();
      message.success(`已恢复跟踪：${r.name || r.code}`);
      load();
    } catch (e: unknown) {
      message.error(e instanceof Error ? e.message : String(e));
    }
  };

  const activeColumns = useMemo(
    () => [
      {
        title: "股票",
        key: "stock",
        width: 190,
        render: (_: unknown, r: WatchlistRow) => (
          <a onClick={() => onOpen(r.code)}>
            <b>{r.name || r.code}</b>
            <Typography.Text type="secondary" style={{ display: "block", fontSize: 11 }}>
              {r.code} · {r.board || "-"}
            </Typography.Text>
          </a>
        ),
      },
      {
        title: "评级",
        key: "rating",
        width: 130,
        render: (_: unknown, r: WatchlistRow) => {
          const rt = getRating(r.code);
          return <RatingMutator code={r.code} current={rt?.rating ?? null} onChanged={() => refreshRatingCache()} />;
        },
      },
      {
        title: "重要变化",
        key: "changes",
        width: 320,
        render: (_: unknown, r: WatchlistRow) =>
          r.changes && r.changes.length > 0 ? (
            <Space direction="vertical" size={0}>
              {r.changes.slice(0, 2).map((c, i) => (
                <ChangeLine key={i} c={c} />
              ))}
            </Space>
          ) : (
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              暂无新变化
            </Typography.Text>
          ),
      },
      {
        title: "路线 / 层级",
        key: "lane",
        width: 140,
        render: (_: unknown, r: WatchlistRow) => (
          <Space size={4}>
            <LaneTag lane={r.lane} />
            <GradeTag tier={r.tier} />
          </Space>
        ),
      },
      {
        title: "确认起点 / 距今",
        key: "start",
        width: 140,
        render: (_: unknown, r: WatchlistRow) => (
          <span>
            <span className="num">{fmtDate(r.confirmed_start_date ?? r.start_date)}</span>
            {r.start_age_days != null && (
              <Typography.Text type="secondary" style={{ marginLeft: 6 }} className="num">
                {r.start_age_days} 日
              </Typography.Text>
            )}
          </span>
        ),
      },
      {
        title: "数据更新至",
        key: "fresh",
        width: 160,
        render: (_: unknown, r: WatchlistRow) => <FreshnessText r={r} />,
      },
      {
        title: "缺失数据",
        key: "miss",
        width: 90,
        render: (_: unknown, r: WatchlistRow) => <MissingTags fields={r.missing_fields ?? []} />,
      },
      {
        title: "最新价格",
        key: "price",
        width: 110,
        align: "right" as const,
        render: (_: unknown, r: WatchlistRow) =>
          r.price_change ? (
            <span className="num">
              {r.price_change.close}
              <Typography.Text style={{ color: chgColor(r.price_change.pct), marginLeft: 6 }}>
                {r.price_change.pct > 0 ? "+" : ""}
                {r.price_change.pct}%
              </Typography.Text>
            </span>
          ) : (
            <Typography.Text type="secondary">未更新</Typography.Text>
          ),
      },
      {
        title: "备注",
        key: "note",
        width: 200,
        render: (_: unknown, r: WatchlistRow) => (
          <Popover
            title="编辑备注"
            open={noteEditing === r.code}
            trigger="click"
            onOpenChange={(o) => {
              if (o) {
                setNoteEditing(r.code);
                setNoteDraft(r.note ?? "");
              }
            }}
            content={
              <Space direction="vertical" style={{ display: "flex", width: 280 }}>
                <Input.TextArea
                  rows={3}
                  value={noteDraft}
                  onChange={(e) => setNoteDraft(e.target.value)}
                  placeholder="写上关注理由、计划动作…"
                  autoFocus
                />
                <Space>
                  <Button size="small" type="primary" onClick={() => saveNote(r)}>
                    保存
                  </Button>
                  <Button size="small" onClick={() => setNoteEditing(null)}>
                    取消
                  </Button>
                </Space>
              </Space>
            }
          >
            <a>
              {r.note ? (
                <Typography.Text ellipsis style={{ maxWidth: 150, display: "inline-block", verticalAlign: "bottom" }}>
                  {r.note}
                </Typography.Text>
              ) : (
                <Typography.Text type="secondary">
                  <EditOutlined /> 添加备注
                </Typography.Text>
              )}
            </a>
          </Popover>
        ),
      },
      {
        title: "操作",
        key: "act",
        width: 160,
        fixed: "right" as const,
        render: (_: unknown, r: WatchlistRow) => (
          <Space size={0}>
            <Tooltip title="打开单票研究">
              <Button size="small" type="text" icon={<EyeOutlined />} onClick={() => onOpen(r.code)} />
            </Tooltip>
            <Popconfirm
              title="移入历史？"
              description="不会删除数据，随时可恢复跟踪。"
              okText="移入历史"
              cancelText="取消"
              onConfirm={() => toHistory(r)}
            >
              <Button size="small" type="text" danger icon={<DeleteOutlined />} />
            </Popconfirm>
          </Space>
        ),
      },
    ],
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [onOpen, noteEditing, noteDraft],
  );

  const historyColumns = [
    {
      title: "股票",
      key: "stock",
      render: (_: unknown, r: WatchlistRow) => (
        <a onClick={() => onOpen(r.code)}>
          <b>{r.name || r.code}</b> <Typography.Text type="secondary">{r.code}</Typography.Text>
        </a>
      ),
    },
    { title: "板块", dataIndex: "board", width: 90, render: (v: string | null) => v || "-" },
    { title: "移除时间", dataIndex: "removed_at", width: 150, render: (v: string | null) => <span className="num">{fmtDt(v)}</span> },
    { title: "加入时间", dataIndex: "added_at", width: 150, render: (v: string | null) => <span className="num">{fmtDt(v)}</span> },
    { title: "备注", dataIndex: "note", render: (v: string | null) => v || <Typography.Text type="secondary">-</Typography.Text> },
    {
      title: "操作",
      key: "act",
      width: 140,
      fixed: "right" as const,
      render: (_: unknown, r: WatchlistRow) => (
        <Space size={0}>
          <Tooltip title="打开单票研究">
            <Button size="small" type="text" icon={<EyeOutlined />} onClick={() => onOpen(r.code)} />
          </Tooltip>
          <Button size="small" type="text" icon={<ArrowRightOutlined />} onClick={() => restore(r)}>
            恢复跟踪
          </Button>
        </Space>
      ),
    },
  ];

  const observeColumns = [
    {
      title: "股票",
      key: "stock",
      render: (_: unknown, r: RatingEntry) => (
        <a onClick={() => onOpen(r.code)}>
          <b>{r.name || r.code}</b> <Typography.Text type="secondary">{r.code}</Typography.Text>
        </a>
      ),
    },
    { title: "板块", dataIndex: "board", width: 90, render: (v: string | null) => v || "-" },
    {
      title: "评级",
      key: "rating",
      width: 140,
      render: (_: unknown, r: RatingEntry) => <RatingMutator code={r.code} current={r.rating} onChanged={() => refreshRatingCache()} />,
    },
    {
      title: "操作",
      key: "act",
      width: 180,
      fixed: "right" as const,
      render: (_: unknown, r: RatingEntry) => (
        <Space size={0}>
          <Tooltip title="打开单票研究">
            <Button size="small" type="text" icon={<EyeOutlined />} onClick={() => onOpen(r.code)} />
          </Tooltip>
        </Space>
      ),
    },
  ];

  const todayChanged = active.filter((r) =>
    r.changes?.some((c) => c.date.slice(0, 10) === todayLocal()),
  ).length;

  return (
    <Space direction="vertical" size={14} style={{ display: "flex" }}>
      {err && <Alert type="error" showIcon message={err} />}

      <div className="page-summary watchlist-summary">
        <Space size={30} wrap>
          <div>
            当前跟踪 <b>{active.length}</b> 只
          </div>
          <div>
            今日有变化 <b style={{ color: todayChanged > 0 ? "#cf1322" : undefined }}>{todayChanged}</b> 只
          </div>
          <div>
            历史记录 <b>{history.length}</b> 只
          </div>
        </Space>
        <div className="watchlist-add" style={{ marginTop: 10 }}>
          <Space.Compact style={{ width: 340 }}>
            <Input
              placeholder="输入 6 位代码直接加入跟踪"
              value={addCode}
              onChange={(e) => setAddCode(e.target.value)}
              onPressEnter={add}
              maxLength={6}
            />
            <Button type="primary" icon={<PlusOutlined />} loading={adding} onClick={add}>
              添加
            </Button>
          </Space.Compact>
          <Typography.Text type="secondary" style={{ marginLeft: 10 }}>
            也可以从「今日值得看 / 因子机会」列表点“跟踪”。机器池变化不影响你的名单；重要变化（新事件、节点推进、户数回升、事件终止等）会出现在“重要变化”一列。
          </Typography.Text>
        </div>
      </div>

      <div className="watchlist-tabs" style={{ display: "flex", gap: 10, alignItems: "center" }}>
        <Button size="small"
          type={view === "active" ? "primary" : "default"}
          onClick={() => setView("active")}
        >
          当前跟踪（{active.length}）
        </Button>
        <Button size="small"
          type={view === "observe" ? "primary" : "default"}
          onClick={() => setView("observe")}
        >
          待观察（{observe.length}）
        </Button>
        <Button size="small"
          type={view === "history" ? "primary" : "default"}
          onClick={() => setView("history")}
        >
          历史记录（{history.length}）
        </Button>
        <div style={{ flex: 1 }} />
        <Tooltip title="重新加载跟踪名单">
          <Button size="small" icon={<ReloadOutlined />} onClick={load}>
            刷新
          </Button>
        </Tooltip>
      </div>

      {view === "active" ? (
        <Table<WatchlistRow>
          rowKey="code"
          columns={activeColumns as never}
          dataSource={active}
          loading={loading}
          size="middle"
          className="watchlist-table"
          tableLayout="fixed"
          scroll={{ x: 1610 }}
          pagination={{ pageSize: 20, showTotal: (t) => `共 ${t} 只` }}
          locale={{
            emptyText: (
              <Empty
                image={Empty.PRESENTED_IMAGE_SIMPLE}
                description={
                  <span>
                    还没有跟踪股票。去「今日值得看」或「因子机会」点<b>跟踪</b>，
                    或在上方输入代码直接添加。
                  </span>
                }
              />
            ),
          }}
        />
      ) : view === "observe" ? (
        <Table<RatingEntry>
          rowKey="code"
          columns={observeColumns}
          dataSource={observe}
          loading={loading}
          size="middle"
          className="watchlist-observe-table"
          scroll={{ x: 700 }}
          pagination={{ pageSize: 20, showTotal: (t) => `共 ${t} 只` }}
          locale={{
            emptyText: (
              <Empty
                image={Empty.PRESENTED_IMAGE_SIMPLE}
                description="暂无待观察：给股票评级「中」或「低」后自动列在这里。"
              />
            ),
          }}
        />
      ) : (
        <Table<WatchlistRow>
          rowKey="code"
          columns={historyColumns}
          dataSource={history}
          loading={loading}
          size="middle"
          scroll={{ x: 900 }}
          pagination={{ pageSize: 20, showTotal: (t) => `共 ${t} 只` }}
          locale={{
            emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无历史记录" />,
          }}
        />
      )}
    </Space>
  );
}
