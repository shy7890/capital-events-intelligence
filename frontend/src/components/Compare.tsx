import { useEffect, useMemo, useState } from "react";
import { cleanEventType } from "./ui";
import {
  Alert,
  Button,
  DatePicker,
  Empty,
  Input,
  Segmented,
  Space,
  Tag,
  Tooltip,
  Typography,
} from "antd";
import { ClearOutlined } from "@ant-design/icons";
import ReactECharts from "echarts-for-react";
import type { EChartsOption } from "echarts";
import dayjs from "dayjs";
import { fetchEvents, fetchKline } from "../api";
import { useCompare } from "./comparectx";

interface Series {
  code: string;
  name: string;
  anchor: string; // 实际锚定日（交易日）
  anchorLabel: string; // 锚定依据说明
  pts: [number, number][]; // [相对交易日, 累计涨幅%]
  color: string;
}

const COLORS = ["#1677ff", "#cf1322", "#fa8c16", "#389e0d", "#d4b106", "#722ed1", "#13c2c2", "#eb2f96"];
const MAX_CODES = 8;

async function buildSeries(code: string, anchorDate: string, window: number): Promise<Series | null> {
  const [k, e] = await Promise.all([fetchKline(code), fetchEvents(code)]);
  const rows = k.rows.filter((r) => Number.isFinite(r.close_qfq) && r.close_qfq > 0);
  if (rows.length === 0) return null;
  const dates = rows.map((r) => r.trade_date);
  let anchor = "";
  let anchorLabel = "区间首日";
  if (anchorDate) {
    anchor = dates.find((d) => d >= anchorDate) ?? "";
    anchorLabel = `指定日 ${anchorDate}`;
  }
  if (!anchor) {
    const sa = e.events
      .filter((ev) => ["S", "A"].includes((ev.grade || "").toUpperCase()))
      .sort((a, b) => (a.start_date < b.start_date ? 1 : -1))[0];
    if (sa) {
      anchor = dates.find((d) => d >= sa.start_date) ?? "";
      anchorLabel = `最近 S/A 事件 ${cleanEventType(sa.event_type)} ${sa.start_date}`;
    }
  }
  if (!anchor) {
    anchor = dates[0];
    anchorLabel = "K线首日";
  }
  const ai = dates.indexOf(anchor);
  const base = rows[ai].close_qfq;
  const lo = Math.max(0, ai - window);
  const hi = Math.min(rows.length - 1, ai + window);
  const pts: [number, number][] = [];
  for (let i = lo; i <= hi; i++) {
    pts.push([i - ai, ((rows[i].close_qfq / base) - 1) * 100]);
  }
  return { code, name: k.name || code, anchor, anchorLabel, pts, color: "" };
}

export default function Compare() {
  const { codes, remove, clear, add } = useCompare();
  const [input, setInput] = useState("");
  const [anchorDate, setAnchorDate] = useState("");
  const [win, setWin] = useState<number>(120);
  const [series, setSeries] = useState<Series[]>([]);
  const [err, setErr] = useState("");
  const [loading, setLoading] = useState(false);

  const reload = (list: string[]) => {
    if (list.length === 0) {
      setSeries([]);
      return;
    }
    setLoading(true);
    setErr("");
    Promise.all(list.map((c) => buildSeries(c, anchorDate, win).catch(() => null)))
      .then((rs) => {
        const ok = rs.filter((r): r is Series => r !== null);
        if (ok.length < list.length)
          setErr(
            `部分代码无 K 线或加载失败：${list.filter((c) => !ok.some((s) => s.code === c)).join("、")}`,
          );
        setSeries(ok.map((s, i) => ({ ...s, color: COLORS[i % COLORS.length] })));
      })
      .finally(() => setLoading(false));
  };

  const addByInput = () => {
    const c = input.trim();
    if (!/^\d{6}$/.test(c)) {
      setErr("请输入 6 位股票代码");
      return;
    }
    if (!add(c)) {
      setErr(codes.includes(c) ? `已添加过 ${c}` : `最多对比 ${MAX_CODES} 只`);
      return;
    }
    setErr("");
    setInput("");
    reload([...codes, c]);
  };

  useEffect(() => {
    if (codes.length) reload(codes);
    else setSeries([]);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [anchorDate, win]);

  const option = useMemo<EChartsOption>(() => {
    return {
      backgroundColor: "transparent",
      tooltip: {
        trigger: "axis",
        valueFormatter: (v) => (typeof v === "number" ? `${v.toFixed(1)}%` : String(v)),
        confine: true,
      },
      legend: { top: 0, type: "scroll" },
      grid: { left: 56, right: 24, top: 36, bottom: 64 },
      xAxis: {
        type: "value",
        name: "相对交易日（0 = 锚定日）",
        nameTextStyle: { color: "#6b7785", fontSize: 11 },
        axisLabel: { color: "#6b7785" },
        splitLine: { lineStyle: { color: "#f0f2f5" } },
      },
      yAxis: {
        type: "value",
        name: "累计涨幅%",
        nameTextStyle: { color: "#6b7785", fontSize: 11 },
        axisLabel: { color: "#6b7785" },
        splitLine: { lineStyle: { color: "#f0f2f5" } },
      },
      dataZoom: [{ type: "slider", bottom: 8, borderColor: "#e5e9ef" }],
      series: series.map((s) => ({
        name: `${s.name} ${s.code}`,
        type: "line" as const,
        showSymbol: false,
        data: s.pts,
        lineStyle: { color: s.color, width: 1.8, type: (series.indexOf(s) % 2 === 0 ? "solid" : "dashed") as "solid" | "dashed" },
        itemStyle: { color: s.color },
        markLine:
          s === series[0]
            ? {
                symbol: "none" as const,
                label: { formatter: "锚定日 0", color: "#6b7785" },
                lineStyle: { color: "#6b7785", type: "dashed" as const },
                data: [{ xAxis: 0 }],
              }
            : undefined,
      })),
    };
  }, [series]);

  return (
    <Space direction="vertical" size={14} style={{ display: "flex" }}>
      <div className="page-summary compare-summary">
        <div className="compare-controls">
          <div className="compare-control-group"><Typography.Text strong>对比股票</Typography.Text><Space wrap size={8}>
          <Input
              placeholder="6位代码，如 123456"
            style={{ width: 160 }}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onPressEnter={addByInput}
          />
          <Button type="primary" onClick={addByInput}>
            加入对比
          </Button>
          </Space></div>
          <div className="compare-control-group"><Typography.Text strong>观察窗口</Typography.Text><Space wrap size={8}>
          <Segmented
            value={win}
            onChange={(v) => setWin(Number(v))}
            options={[
              { label: "±60 交易日", value: 60 },
              { label: "±120 交易日", value: 120 },
              { label: "±250 交易日", value: 250 },
            ]}
          />
          </Space></div>
          <div className="compare-control-group"><Typography.Text strong>统一锚点</Typography.Text><Space wrap size={8}>
          <Tooltip title="留空=各自最近 S/A 事件日作锚点">
            <DatePicker value={anchorDate ? dayjs(anchorDate) : null} onChange={(d) => setAnchorDate(d ? d.format("YYYY-MM-DD") : "")} allowClear placeholder="默认各自最近事件日" />
          </Tooltip>
          {codes.length > 0 && (
            <Button size="small" icon={<ClearOutlined />} onClick={clear}>
              清空
            </Button>
          )}
          {loading && <Tag color="processing">计算中…</Tag>}
          </Space></div>
        </div>

        {codes.length > 0 && (
          <div style={{ marginTop: 10, display: "flex", flexWrap: "wrap", gap: 6 }}>
            {series.map((s) => (
              <Tag
                key={s.code}
                closable
                onClose={() => {
                  remove(s.code);
                  reload(codes.filter((c) => c !== s.code));
                }}
                color="default"
                className="compare-chip"
                style={{ marginInlineEnd: 0, borderColor: s.color }}
              >
                <span className="compare-chip-label" style={{ color: s.color }}>
                  <i aria-hidden="true" style={{ background: s.color }} />
                  {s.name} {s.code}
                </span>
                <Tooltip title={`锚定依据：${s.anchorLabel}（${s.anchor}）`}>
                  <span className="compare-chip-date">
                    {s.anchor.slice(5) || "首日"}
                  </span>
                </Tooltip>
              </Tag>
            ))}
          </div>
        )}

        <Typography.Text type="secondary" style={{ display: "block", marginTop: 8 }}>
          锚点默认取各自「最近一次 S/A 资本运作事件日」，之后叠加前复权累计涨幅；「0」竖线为事件日。
          输入代码即可加入，最多支持 8 只股票。
        </Typography.Text>
      </div>

      {err && <Alert type="warning" showIcon message={err} />}

      {series.length > 0 ? (
        <div style={{ background: "#fff", borderRadius: 8, padding: 8, border: "1px solid #eaeef2" }}>
          <ReactECharts option={option} style={{ height: 480 }} notMerge lazyUpdate />
        </div>
      ) : (
        !loading && (
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description={
              <Space direction="vertical" size={4}>
                <span>在上方输入 6 位代码并点击<b>加入对比</b></span>
                <Typography.Text type="secondary">支持 2–8 只，便于判断哪只更早/更稳地兑现涨势</Typography.Text>
              </Space>
            }
          />
        )
      )}
    </Space>
  );
}
