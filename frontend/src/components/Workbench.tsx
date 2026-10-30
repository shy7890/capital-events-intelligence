import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Alert,
  Button,
  Collapse,
  Descriptions,
  Empty,
  Grid,
  Input,
  message,
  Space,
  Spin,
  Tag,
  Timeline,
  Tooltip,
  Typography,
} from "antd";
import {
  ArrowLeftOutlined,
  DownloadOutlined,
  FileTextOutlined,
  StarOutlined,
} from "@ant-design/icons";
import { EventDetail, fetchEvents, fetchHolder, fetchKline, HolderRow, KlineResp, addWatchlist } from "../api";
import StockChart from "./StockChart";
import { cleanEventType, GradeTag, TerminatedTag, fmtDate } from "./ui";
import { useWatchCodes, refreshWatchCache } from "./watchcache";
import { RatingTag, RatingMutator } from "./RatingUI";
import { useRatings, refreshRatingCache } from "./ratingcache";

interface Props {
  code: string;
  onCodeChange: (c: string) => void;
  onBack?: () => void;
  backLabel?: string;
}

export default function Workbench({ code, onCodeChange, onBack, backLabel }: Props) {
  const [input, setInput] = useState(code);
  const [kline, setKline] = useState<KlineResp | null>(null);
  const [events, setEvents] = useState<EventDetail[]>([]);
  const [holders, setHolders] = useState<HolderRow[]>([]);
  const [err, setErr] = useState("");
  const [loading, setLoading] = useState(false);
  const [added, setAdded] = useState(false);
  const { codes } = useWatchCodes();
  const screens = Grid.useBreakpoint();
  const isSmall = !screens.lg;
  const { get: getRating } = useRatings();
  const rating = getRating(code);

  const load = useCallback(
    (c: string) => {
      const trimmed = c.trim();
      if (!trimmed) return;
      setLoading(true);
      setErr("");
      setKline(null);
      setEvents([]);
      setHolders([]);
      Promise.all([fetchKline(trimmed), fetchEvents(trimmed), fetchHolder(trimmed)])
        .then(([k, e, h]) => {
          setKline(k);
          setEvents(e.events ?? []);
          setHolders(h.rows ?? []);
        })
        .catch((ex: unknown) => setErr(ex instanceof Error ? ex.message : String(ex)))
        .finally(() => setLoading(false));
    },
    [],
  );

  useEffect(() => {
    setInput(code);
    load(code);
    setAdded(codes.has(code));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [code]);

  useEffect(() => {
    if (code) setAdded(codes.has(code));
  }, [code, codes]);

  const doLoad = () => {
    const c = input.trim();
    if (!c) return;
    onCodeChange(c);
    if (c === code) load(c);
    setAdded(codes.has(c));
  };

  const addTrack = async () => {
    try {
      await addWatchlist(code);
      refreshWatchCache();
      message.success(`已加入跟踪：${kline?.name || code}`);
      setAdded(true);
    } catch (e: unknown) {
      message.error(e instanceof Error ? e.message : String(e));
    }
  };

  const sortedEvents = useMemo(
    () => [...events].sort((a, b) => (a.start_date < b.start_date ? 1 : -1)),
    [events],
  );

  const klineLatest = kline?.rows?.length ? kline.rows[kline.rows.length - 1].trade_date : null;
  const holderLatest = holders.length ? holders[holders.length - 1].disclose_date : null;
  const latestEv = sortedEvents[0] ?? null;

  return (
    <Space direction="vertical" size={14} style={{ display: "flex" }}>
      <div className="page-summary workbench-summary">
        {onBack && (
          <Button
            type="link"
            icon={<ArrowLeftOutlined />}
            onClick={onBack}
            style={{ paddingLeft: 0, marginBottom: 6 }}
          >
            返回{backLabel ? ` ${backLabel}` : "列表"}
          </Button>
        )}
        <Space wrap size={8}>
          <Input
            value={input}
            placeholder="股票代码，如 123456"
            style={{ width: 220 }}
            onChange={(e) => setInput(e.target.value)}
            onPressEnter={doLoad}
            allowClear
          />
          <Button
            type="primary"
            onClick={doLoad}
            loading={loading}
            disabled={!/^\d{6}$/.test(input.trim()) && input.trim().length < 1}
          >
            加载
          </Button>
          <Typography.Text type="secondary">或从「今日值得看 / 因子机会 / 我的跟踪」点击任意股票进入</Typography.Text>
        </Space>

        {err && (
          <Alert
            style={{ marginTop: 10 }}
            type="error"
            showIcon
            message="加载失败"
            description={err}
          />
        )}

        {kline && (
          <div className="workbench-stock-info" style={{ marginTop: 14 }}>
            <Descriptions size="small" column={isSmall ? 1 : 3} colon={false} title={<b style={{ fontSize: 16 }}>{kline.name || kline.code}</b>}>
              {!isSmall && <Descriptions.Item label="代码">{kline.code}</Descriptions.Item>}
              <Descriptions.Item label="最新事件">
                {latestEv ? (
                  <Space size={6} wrap>
                    <GradeTag grade={latestEv.grade} />
                    <span>{cleanEventType(latestEv.event_type)}</span>
                    <Tag>{latestEv.status || "-"}</Tag>
                    <Typography.Text type="secondary" className="num">{fmtDate(latestEv.start_date)}</Typography.Text>
                  </Space>
                ) : (
                  <Typography.Text type="secondary">暂无资本运作事件</Typography.Text>
                )}
              </Descriptions.Item>
              <Descriptions.Item label="数据">
                <Space size={6} wrap>
                  <Tag color={klineLatest ? "blue" : "default"}>
                    K线 {klineLatest ? fmtDate(klineLatest) : "无"}
                  </Tag>
                  <Tag color={holderLatest ? "orange" : "default"}>
                    股东户数 {holderLatest ? fmtDate(holderLatest) : "无"}
                  </Tag>
                  {holders.length === 0 && (
                    <Tooltip title="该股无股东户数记录，f 因子无法评估，不属于未知=无信号">
                      <Tag color="gold">户数待补</Tag>
                    </Tooltip>
                  )}
                </Space>
              </Descriptions.Item>
              {isSmall && <Descriptions.Item label="代码">{kline.code}</Descriptions.Item>}
              <Descriptions.Item label="操作">
                <Space size={4}>
                  <RatingMutator
                    code={code}
                    current={rating?.rating ?? null}
                    onChanged={() => refreshRatingCache()}
                  />
                  <Tooltip title={codes.has(code) ? "已在跟踪名单" : "加入我的跟踪，重要变化实时提醒"}>
                    <Button
                      size="small"
                      icon={<StarOutlined />}
                      onClick={addTrack}
                      disabled={added}
                    >
                      {added ? "已跟踪" : "加入跟踪"}
                    </Button>
                  </Tooltip>
                  <Tooltip title="导出素材包（Markdown，适合喂聊天 AI）">
                    <a href={`/api/export/${encodeURIComponent(kline.code)}?fmt=md`} download>
                      <Button size="small" icon={<FileTextOutlined />}>导出 MD</Button>
                    </a>
                  </Tooltip>
                  <Tooltip title="导出素材包（JSON，适合喂程序）">
                    <a href={`/api/export/${encodeURIComponent(kline.code)}?fmt=json`} download>
                      <Button size="small" icon={<DownloadOutlined />}>导出 JSON</Button>
                    </a>
                  </Tooltip>
                </Space>
              </Descriptions.Item>
            </Descriptions>
          </div>
        )}
      </div>

      {!err && !kline && !loading && (
        <Empty
          style={{ marginTop: 40 }}
          description={
            <Space direction="vertical" size={4}>
              <b>输入股票代码进入研究</b>
              <Typography.Text type="secondary">
                支持：K 线 + 事件标注 + 股东户数 + 工艺流程节点；可从任意列表点击进入
              </Typography.Text>
            </Space>
          }
        />
      )}

      {loading && (
        <div style={{ textAlign: "center", padding: 40 }}>
          <Spin tip="正在读取 K 线、事件与户数…">
            <div style={{ height: 60 }} />
          </Spin>
        </div>
      )}

      {kline && kline.rows.length > 0 && (
        <div className="chart-panel workbench-chart"><StockChart name={kline.name || kline.code} rows={kline.rows} events={events} holders={holders} code={kline.code} /></div>
      )}
      {kline && kline.rows.length === 0 && (
        <Alert type="warning" showIcon message="该股在库内无 K 线数据（可能上市过晚或数据未回填）。K 线是最基础证据，建议先补数据。" />
      )}

      {kline && (
        <div style={{ display: "grid", gridTemplateColumns: isSmall ? "1fr" : "1fr 1fr", gap: 14 }}>
          <div className="chart-panel workbench-event-panel">
            <Typography.Title level={5} style={{ marginTop: 0 }}>
              工艺流程时间线（{sortedEvents.length} 个事件 · 按起始日倒序）
            </Typography.Title>
            {sortedEvents.length === 0 ? (
              <Typography.Text type="secondary">暂无资本运作事件</Typography.Text>
            ) : (
              <Timeline
                items={sortedEvents.map((ev) => ({
                  color: eventColor(ev),
                  label: <Typography.Text type="secondary" className="num">{fmtDate(ev.start_date)}</Typography.Text>,
                  children: (
                    <div>
                      <Space size={6} wrap>
                        <GradeTag grade={ev.grade} />
                        <b>{cleanEventType(ev.event_type)}</b>
                        <TerminatedTag status={ev.status} />
                      </Space>
                      <div style={{ marginTop: 6, display: "flex", flexWrap: "wrap", gap: 6 }}>
                        {(ev.nodes ?? [])
                          .slice()
                          .sort((a, b) => (a.node_date < b.node_date ? -1 : 1))
                          .map((nd, i) => (
                            <Tooltip key={i} title={nd.title || nd.node_type}>
                              <Tag style={{ marginInlineEnd: 0 }}>
                                {nd.node_type}
                                <Typography.Text type="secondary" className="num" style={{ fontSize: 11, marginLeft: 4 }}>
                                  {fmtDate(nd.node_date)}
                                </Typography.Text>
                              </Tag>
                            </Tooltip>
                          ))}
                      </div>
                    </div>
                  ),
                }))}
              />
            )}
          </div>

          <div className="chart-panel workbench-guide">
            <Collapse ghost items={[{ key: "guide", label: "怎么用这张图（买入建议留给决策人）", children: <Space direction="vertical" size={6} style={{ display: "flex", fontSize: 13 }} className="mutedText">
              <span>① <b>K 线</b>：前复权日 K，事件标记点按 S(红)/A(橙)/B(蓝)着色，点击/悬停看公告。</span>
              <span>② <b>成交量</b>：红涨绿跌，看起涨期是否放量。</span>
              <span>③ <b>股东户数</b>：普通折线；<b style={{ color: "#cf1322" }}>下降 = 筹码集中</b>，回升 = 散户回流（危险信号）。数据为 0 不显示。</span>
              <span>④ <b>事件泳道</b>：每个事件一行，圆点为流程节点（筹划/方案/股东大会…），点在时间轴上的位置 = 公告日。</span>
              <span>⑤ <b>导航</b>：左右拖动平移时间窗口；滚轮不缩放；快捷范围 60/120/250 日/全部。</span>
              <span>⑥ <b>导出</b>：素材包含全部归档数据，交给 AI 做完整复盘。</span>
            <Typography.Paragraph type="secondary" style={{ marginTop: 12, fontSize: 12 }}>
              数据不足的字段（如缺户数）显示“待补”而不是当作“无信号”；真实数据以库内为准。
            </Typography.Paragraph>
            </Space> }]} />
          </div>
        </div>
      )}
    </Space>
  );
}

function eventColor(ev: EventDetail): string {
  const g = (ev.grade || "").toUpperCase();
  if (g === "S") return "#cf1322";
  if (g === "A") return "#fa8c16";
  if (g === "B") return "#1677ff";
  if ((ev.status || "") === "终止") return "#389e0d";
  return "gray";
}
