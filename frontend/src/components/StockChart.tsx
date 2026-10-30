import { useEffect, useMemo, useRef, useState } from "react";
import ReactECharts from "echarts-for-react";
import type { EChartsOption } from "echarts";
import { Button, Modal, Segmented, Space, Tag, Typography } from "antd";
import type { EventDetail, EventNode, HolderRow, KlineRow } from "../api";
import { cleanEventType, fmtCompact } from "./ui";

interface Props {
  name: string;
  rows: KlineRow[];
  events: EventDetail[];
  holders: HolderRow[];
  code: string;
}

const GRADE_COLOR: Record<string, string> = {
  S: "#9f1239",
  A: "#b54708",
  B: "#175cd3",
  T: "#027a48",
};

const UP = "#d92d20"; // A股红涨
const DOWN = "#078a55"; // A股绿跌

function gradeColor(g: string): string {
  return GRADE_COLOR[(g || "").toUpperCase()] ?? "#1677ff";
}

/** 弱信号节点：非资本运作流程本身的行为公告（减持/回购/高管变动/子公司等），K线标记弱化 */
function isWeakNode(t: string): boolean {
  return ["减持动向", "回购动作", "高管变动", "设立子公司", "权益变动披露", "股权质押", "解禁"].some((s) => (t ?? "").includes(s));
}

function adjFactor(r: KlineRow): number {
  if (!r.close || !Number.isFinite(r.close) || r.close === 0) return 1;
  return r.close_qfq / r.close;
}

function firstIdxOnOrAfter(dates: string[], target: string): number {
  if (!target) return -1;
  for (let i = 0; i < dates.length; i++) {
    if (dates[i] >= target) return i;
  }
  return dates.length > 0 ? dates.length - 1 : -1;
}

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

type Range = "60" | "120" | "250" | "all";

/** 户数专用 tooltip：只显示股东户数信息（变化趋势/涨幅/环比），不混入 K 线。 */
function _holderTip(date: string, h: HolderRow | undefined): string {
  if (!h) return `<b>${escapeHtml(date)}</b><br/>暂无股东户数披露`;
  const pct = h.pct_change == null ? null : Number(h.pct_change);
  const chgC = pct == null ? "" : pct >= 0 ? "#d4b106" : "#cf1322";
  const trend: string[] = [`<b>${escapeHtml(date)}</b>`];
  trend.push(`股东户数（万户）<b>${h.holder_num == null ? "-" : (h.holder_num / 10000).toFixed(2)}</b>（${fmtCompact(h.holder_num)} 户）`);
  if (pct == null) {
    trend.push(`较上期变化：<span style="color:#526070">未知</span>`);
  } else {
    const arrow = pct >= 0 ? "▲" : "▼";
    trend.push(
      `较上期变化 <b style="color:${chgC}">${arrow} ${Math.abs(pct).toFixed(2)}%</b>`,
    );
    if (pct <= -8) trend.push(`<span style="color:#cf1322">筹码集中信号</span>（户数大幅减少）`);
    else if (pct >= 8) trend.push(`<span style="color:#d4b106">散户回流信号</span>（户数大幅增加）`);
    else if (pct < 0) trend.push(`<span style="color:#cf1322">户数减少·筹码向少数集中</span>`);
    else trend.push(`<span style="color:#d4b106">户数增加·筹码分散</span>`);
  }
  return trend.join("<br/>");
}

export default function StockChart({ name, rows, events, holders }: Props) {
  const [range, setRange] = useState<Range>("250");
  const [node, setNode] = useState<{ ev: EventDetail; nd: EventNode } | null>(null);
  const chartWrapRef = useRef<HTMLDivElement>(null);

  // ECharts 会拦截 canvas 上的 wheel 事件（roam/zoom controller 在命中图表区域时
  // 必然 preventDefault），导致触控板上滑/下滑无法滚动页面。
  // 本图表数据缩放/平移均移动鼠标触发（zoomOnMouseWheel=false / moveOnMouseWheel=false），
  // 因此无需 wheel；在捕获阶段拦截后停止传播即可恢复页面滚动，
  // 同时 ECharts 内不因 wheel 产生任何缩放行为。
  useEffect(() => {
    const el = chartWrapRef.current;
    if (!el) return;
    const stop = (e: WheelEvent) => {
      e.stopImmediatePropagation();
    };
    el.addEventListener("wheel", stop, { capture: true, passive: true });
    return () => el.removeEventListener("wheel", stop, { capture: true } as EventListenerOptions);
  }, []);

  const { option, laneRows, evNames } = useMemo(() => {
    const dates = rows.map((r) => r.trade_date);
    const dateToIdx = new Map<string, number>();
    dates.forEach((d, i) => dateToIdx.set(d, i));

    // ① K线（前复权）
    const candles = rows.map((r) => {
      const f = adjFactor(r);
      return [r.open * f, r.close_qfq, r.low * f, r.high * f];
    });

    // 事件节点 → 主图 markPoint
    // 弱信号节点（减持/回购/高管变动/设立子公司等非资本运作行为）用灰色小点弱化；
    // 真正的资本运作流程节点（方案披露/股东大会/受理/过会…）用 S/A/B 等级色。
    const markData: Array<Record<string, unknown>> = [];
    const nodesByDate = new Map<string, Array<EventNode & { grade: string; weak: boolean }>>();
    for (const ev of events) {
      for (const nd of ev.nodes ?? []) {
        const idx = firstIdxOnOrAfter(dates, nd.node_date);
        if (idx < 0) continue;
        const r = rows[idx];
        const y = r.high * adjFactor(r) * 1.012;
        const weak = isWeakNode(nd.node_type);
        const extra = { ...nd, grade: (ev.grade || "").toUpperCase(), weak };
        const bucket = nodesByDate.get(dates[idx]) ?? [];
        bucket.push(extra);
        nodesByDate.set(dates[idx], bucket);
        markData.push({
          name: nd.node_type,
          coord: [dates[idx], y],
          value: "",
          symbol: "circle",
          symbolSize: weak ? 5 : 9,
          itemStyle: {
            color: weak ? "#8c99a6" : gradeColor(ev.grade),
            borderColor: weak ? "#c9d3dc" : "#fff",
            borderWidth: weak ? 1 : 1.2,
            opacity: weak ? 0.75 : 1,
          },
        });
      }
    }

    // ④ 事件泳道：每个事件一行（y = 事件序号），节点按日期画点
    const evNames = events.map((ev) => {
      const t = cleanEventType(ev.event_type);
      const short = t.length > 12 ? t.slice(0, 12) + "…" : t;
      return `${ev.start_date.slice(5)} ${short}（${ev.grade || "-"}）`;
    });
    const laneRows: Array<{ value: [string, number]; evIdx: number; ndIdx: number; nodeType: string; level: string }> = [];
    events.forEach((ev, evIdx) => {
      (ev.nodes ?? []).forEach((nd, ndIdx) => {
        laneRows.push({
          value: [nd.node_date.slice(0, 10), evIdx],
          evIdx,
          ndIdx,
          nodeType: nd.node_type,
          level: (ev.grade || "").toUpperCase(),
        });
      });
    });

    // ② 成交量
    const volumes = rows.map((r) => {
      const f = adjFactor(r);
      const up = r.close_qfq >= r.open * f;
      return { value: r.volume, itemStyle: { color: up ? UP : DOWN, opacity: 0.65 } };
    });

    // ③ 股东户数（同花顺风格：蓝柱固定像素宽 + 橙线股价，与 K 线共享时间比例尺）
    // 披露日 → K线首个 >= 该披露日的交易日 index；柱落在真实交易日位置，上下十字线可对齐。
    const holderData: Array<number | null> = new Array(dates.length).fill(null);
    const holderByDate = new Map<string, HolderRow>();
    for (const h of holders) {
      const idx = firstIdxOnOrAfter(dates, h.disclose_date);
      if (idx < 0 || holderData[idx] != null) continue;
      holderData[idx] = h.holder_num == null ? null : h.holder_num / 10000;
      holderByDate.set(dates[idx], h);
    }
    // 当前可视窗口内的披露期数（用于柱顶数值标签显隐判断）
    const windowStartIdx = range === "all" ? 0 : Math.max(0, dates.length - Number(range));
    let holderDiscloseCount = 0;
    for (const d of holderByDate.keys()) {
      const i = dates.indexOf(d);
      if (i < 0 || i >= windowStartIdx) holderDiscloseCount++;
    }
    // 股价（前复权收盘）与户数披露日同点
    const holderPrice: Array<number | null> = new Array(dates.length).fill(null);
    for (let i = 0; i < dates.length; i++) {
      if (holderData[i] != null) holderPrice[i] = rows[i].close_qfq;
    }

    const startIdx = range === "all" ? 0 : Math.max(0, dates.length - Number(range));
    const startValue = dates[startIdx];

    const yAxisCommon = {
      splitLine: { lineStyle: { color: "#e9eef5" } },
      axisLabel: { color: "#667085", fontSize: 11 },
      nameTextStyle: { color: "#667085", fontSize: 11 },
      nameGap: 8,
    };
    const xAxisCommon = {
      axisLine: { lineStyle: { color: "#dce3ec" } },
      axisLabel: { color: "#667085", fontSize: 11 },
    };

    const option: EChartsOption = {
      backgroundColor: "transparent",
      animation: false,
      legend: {
        data: ["K线", "成交量", "股东户数(万户)", "股价(元)", "事件节点"],
        top: 2,
        left: 8,
        icon: "circle",
        textStyle: { color: "#526070", fontSize: 12 },
        selectedMode: true,
      },
      tooltip: {
        trigger: "axis",
        axisPointer: {
          type: "cross",
          crossStyle: { color: "#b0b8c4" },
          label: { backgroundColor: "#eaf2fb", color: "#172033" },
        },
        // 注意：不要加 mousewheel 触发（会使工具提示拦截触控板滚动页面）；类型限制下用断言
        triggerOn: "mousemove|click" as "mousemove|click|mousewheel",
        enterable: true,
        // 防老跳：鼠标快速扫过不弹；停住 ~250ms 才出现；移开后 ~150ms 消失
        showDelay: 250,
        hideDelay: 150,
        confine: true,
        // 防飘：position:"inside" 让提示框始终留在图表内部（不贴边翻转），
        // 配合 showDelay 250ms 防扫跳、confine 防越界——鼠标快速移动时提示只做小幅
        // 跟手平移，不再满屏大幅跳动。若仍觉飘，再改为固定数组（代价：会挡住局部）。
        position: "inside",
        extraCssText:
          "background:#fff;border:1px solid #dce3ec;color:#172033;max-width:460px;white-space:normal;box-shadow:0 8px 24px rgba(16,24,40,.12);border-radius:8px;padding:8px 10px;",
        formatter: (paramsAll: unknown) => {
          const psRaw = paramsAll as unknown[];
          const ps = psRaw.map((p) => (p ?? {}) as Record<string, unknown>);
          const date = String(ps[0]?.axisValue ?? "");
          // 分区：指向户数柱（holder 系列命中且值为柱高）时只展示户数信息
          const holderParam = ps.find((p) => p.seriesId === "holder" && p.value != null);
          if (holderParam) {
            return _holderTip(date, holderByDate.get(date));
          }
          const i = dateToIdx.get(date);
          const parts: string[] = [`<b>${escapeHtml(date)}</b>`];
          if (i != null) {
            const r = rows[i];
            const chg = i > 0 ? ((r.close_qfq / rows[i - 1].close_qfq - 1) * 100).toFixed(2) : "0.00";
            parts.push(
              `开 ${r.open.toFixed(2)}　高 ${r.high.toFixed(2)}　低 ${r.low.toFixed(2)}　收(前复权) <b>${r.close_qfq.toFixed(2)}</b>　涨跌 <b style="color:${
                Number(chg) >= 0 ? UP : DOWN
              }">${chg}%</b>`,
            );
            parts.push(`成交量 ${fmtCompact(r.volume)}　换手率 ${r.turnover ?? "-"}%`);
            // 距最新交易日的累计涨幅（用户常用：看到底涨了多少）
            const lastI = rows.length - 1;
            if (lastI > i && rows[lastI].close_qfq > 0) {
              const hot = ((rows[lastI].close_qfq / r.close_qfq - 1) * 100).toFixed(2);
              parts.push(
                `距最新日（${escapeHtml(rows[lastI].trade_date)}）累计 <b style="color:${Number(hot) >= 0 ? UP : DOWN}">${hot}%</b>`,
              );
            }
          }
          const nds = nodesByDate.get(date);
          if (nds && nds.length) {
            parts.push(
              `<div style="margin-top:4px;border-top:1px dashed #eaeef2;padding-top:4px">` +
                nds
                  .map((nd) => {
                    const link = nd.pdf_url
                      ? ` <a href="${escapeHtml(String(nd.pdf_url))}" target="_blank" rel="noopener" style="color:#1677ff;text-decoration:underline">原文</a>`
                      : "";
                    const dotColor = nd.weak ? "#8c99a6" : gradeColor(nd.grade);
                    return `<span style="color:${dotColor}">●</span> [${nd.grade}] <span style="color:${nd.weak ? "#6b7785" : ""}">${escapeHtml(nd.node_type)}</span>（${escapeHtml(nd.node_date)}）：${escapeHtml(nd.title ?? "")}${link}${nd.weak ? '<br/><span style="color:#9aa4b2;font-size:11px">弱信号（非资本运作流程）</span>' : ""}`;
                  })
                  .join("<br/>") +
              `</div>`,
            );
          }
          return parts.join("<br/>");
        },
      },
      axisPointer: { link: [{ xAxisIndex: [0, 1, 3] }], snap: true },      grid: [
        { left: 64, right: 20, top: 30, height: "44%" },
        { left: 64, right: 20, top: "56%", height: "11%" },
        { left: 64, right: 56, top: "69%", height: "13%" },
        { left: 64, right: 20, top: "85%", height: "9%" },
      ],
      xAxis: [
        {
          type: "category", data: dates, gridIndex: 0, boundaryGap: true,
          axisLabel: { ...xAxisCommon.axisLabel, show: false }, axisLine: xAxisCommon.axisLine, splitLine: { show: false },
        },
        {
          type: "category", data: dates, gridIndex: 1, boundaryGap: true,
          axisLabel: { ...xAxisCommon.axisLabel, show: false }, axisLine: xAxisCommon.axisLine, splitLine: { show: false },
        },
        {
          type: "category", data: dates, gridIndex: 2, boundaryGap: false,
          axisLabel: { ...xAxisCommon.axisLabel, show: false }, axisLine: xAxisCommon.axisLine, splitLine: { show: false },
        },
        {
          type: "category", data: dates, gridIndex: 3, boundaryGap: true,
          axisLabel: xAxisCommon.axisLabel, axisLine: xAxisCommon.axisLine, splitLine: { show: false },
        },
      ],
      yAxis: [
        {
          scale: true, gridIndex: 0, ...yAxisCommon,
          axisLabel: { ...yAxisCommon.axisLabel, formatter: (v: number) => v.toFixed(1) },
        },
        {
          scale: true, gridIndex: 1, splitNumber: 2, ...yAxisCommon,
          axisLabel: { ...yAxisCommon.axisLabel, formatter: (v: number) => fmtCompact(v) },
          name: "成交量",
        },
        {
          scale: true, gridIndex: 2, splitNumber: 3, ...yAxisCommon,
          type: "value", min: 0,
          axisLabel: { ...yAxisCommon.axisLabel, formatter: (v: number) => v.toFixed(1) },
          name: "股东户数(万户)",
        },
        {
          scale: true, gridIndex: 2, splitNumber: 3, ...yAxisCommon,
          show: true, position: "right",
          axisLabel: { ...yAxisCommon.axisLabel, formatter: (v: number) => v.toFixed(1) },
          name: "股价",
          nameTextStyle: { ...yAxisCommon.nameTextStyle },
        },
        {
          type: "category", gridIndex: 3, data: evNames,
          name: "事件",
          splitLine: { show: false },
          axisLabel: {
            color: "#6b7785", fontSize: 10,
            formatter: (v: string) => (v.length > 18 ? v.slice(0, 18) + "…" : v),
          },
          axisTick: { show: false },
          axisLine: { show: false },
          nameTextStyle: { color: "#9aa4b2", fontSize: 10 },
          nameGap: 6,
        },
      ],
      dataZoom: [
        {
          type: "inside",
          xAxisIndex: [0, 1, 2, 3],
          startValue,
          end: 100,
          zoomOnMouseWheel: false, // 滚轮/触控板不缩放
          moveOnMouseMove: true, // 左右拖动平移
          moveOnMouseWheel: false, // 触控板两指也不缩放
        },
        {
          type: "slider",
          xAxisIndex: [0, 1, 2, 3],
          bottom: 0,
          height: 16,
          borderColor: "#e5e9ef",
          backgroundColor: "#f5f7fa",
          fillerColor: "rgba(22,119,255,0.12)",
          handleStyle: { color: "#1677ff" },
          textStyle: { color: "#6b7785", fontSize: 10 },
          brushSelect: false,
        },
      ],
      series: [
        {
          id: "k",
          name: "K线",
          type: "candlestick",
          xAxisIndex: 0,
          yAxisIndex: 0,
          data: candles,
          itemStyle: { color: UP, color0: DOWN, borderColor: UP, borderColor0: DOWN },
          markPoint: { data: markData as never, silent: false, label: { show: false } },
        },
        {
          id: "vol",
          name: "成交量",
          type: "bar",
          xAxisIndex: 1,
          yAxisIndex: 1,
          data: volumes,
          barWidth: "62%",
        },
        {
          id: "holder",
          name: "股东户数(万户)",
          type: "bar",
          xAxisIndex: 2,
          yAxisIndex: 2,
          data: holderData,
          barWidth: 14,
          barMaxWidth: 18,
          // 柱顶数值标签：披露日少（≤12）才显示（同花顺式）；密集时隐藏防重叠，悬停 tooltip 可看
          label: {
            show: holderDiscloseCount <= 12,
            position: "top",
            color: "#1d5fa7",
            fontSize: 11,
            fontWeight: 600,
            formatter: (params: unknown) => {
              const v = (params as { value?: number | null })?.value;
              return v == null ? "" : Number(v).toFixed(2);
            },
          },
          itemStyle: {
            color: "#1d5fa7",
            opacity: 0.9,
            borderRadius: [3, 3, 0, 0],
          },
          emphasis: {
            focus: "series",
            itemStyle: { opacity: 1 },
          },
          z: 3,
        },
        {
          id: "holderprice",
          name: "股价(元)",
          type: "line",
          xAxisIndex: 2,
          yAxisIndex: 3,
          data: holderPrice,
          symbol: "circle",
          symbolSize: 6,
          connectNulls: true,
          lineStyle: { color: "#e8873a", width: 1.8 },
          itemStyle: { color: "#e8873a", borderColor: "#fff", borderWidth: 1.2 },
          emphasis: { focus: "series" },
          z: 4,
        },
        {
          id: "events",
          name: "事件节点",
          type: "scatter",
          xAxisIndex: 3,
          yAxisIndex: 4,
          data: laneRows.map((x) => ({
            value: x.value,
            evIdx: x.evIdx,
            ndIdx: x.ndIdx,
            nodeType: x.nodeType,
            level: x.level,
          })),
          symbol: "pin",
          symbolSize: 13,
          itemStyle: { color: "#1d5fa7", opacity: 0.92 },
          label: {
            show: false,
          },
          emphasis: { scale: 1.4 },
        } as never,
      ],
    };
    return { option, laneRows, evNames };
  }, [name, rows, events, holders, range]);

  return (
    <div>
      <div
        className="chart-toolbar"
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          marginBottom: 6,
          flexWrap: "wrap",
          gap: 8,
        }}
      >
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          悬停十字线看详情 · 左右拖动平移 · 滚轮/触控板不缩放 · 点击事件点看公告
        </Typography.Text>
        <Segmented
          size="small"
          value={range}
          onChange={(v) => setRange(v as Range)}
          options={[
            { label: "近 60 日", value: "60" },
            { label: "近 120 日", value: "120" },
            { label: "近 250 日", value: "250" },
            { label: "全部", value: "all" },
          ]}
        />
      </div>
      <div ref={chartWrapRef} className="echarts-surface" style={{ background: "#fff", borderRadius: 8, border: "1px solid #eaeef2", padding: "4px 4px 2px" }}>
        <ReactECharts
          option={option}
          notMerge
          lazyUpdate
          style={{ height: 620, width: "100%" }}
          opts={{ renderer: "canvas" }}
          onEvents={{
            click: (params: unknown) => {
              const p = params as {
                seriesId?: string;
                data?: { value?: [string, number]; evIdx?: number; ndIdx?: number; nodeType?: string };
              };
              if (p?.seriesId !== "events" || !p.data) return;
              const ev = events[p.data.evIdx ?? -1];
              const nd = ev?.nodes?.[p.data.ndIdx ?? -1];
              if (ev && nd) setNode({ ev, nd });
            },
          }}
        />
      </div>
      <Modal
        open={!!node}
        onCancel={() => setNode(null)}
        footer={null}
        title={
          node && (
            <Space size={6}>
              <span style={{ color: gradeColor(node.ev.grade) }}>●</span>
              <b>{node.nd.node_type}</b>
              <Tag color="default">{node.nd.node_date}</Tag>
              <Tag>{cleanEventType(node.ev.event_type)}</Tag>
            </Space>
          )
        }
      >
        {node && (
          <Space direction="vertical" size={8} style={{ display: "flex" }}>
            <Typography.Text strong>{node.nd.title || "（无公告标题）"}</Typography.Text>
            {node.nd.pdf_url ? (
              <a href={node.nd.pdf_url} target="_blank" rel="noopener noreferrer">
                查看公告原文 PDF ↗
              </a>
            ) : (
              <Typography.Text type="secondary">无原文链接</Typography.Text>
            )}
          </Space>
        )}
      </Modal>
    </div>
  );
}
