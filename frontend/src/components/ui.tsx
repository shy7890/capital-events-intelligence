import { Tag, Tooltip } from "antd";

/** 清洗事件类型展示串：去除历史导入的 v6: 前缀（如 "v6:重大资产重组" → "重大资产重组"）。 */
export function cleanEventType(t?: string | null): string {
  return (t || "未命名事件").replace(/^v6[:：]\s*/, "");
}

/**
 * 公共展示组件：等级徽章、格式化、字段解释。
 * 颜色约定（层级专用，不与涨跌色混用）：
 *   S=红(醒目) A=橙 B=蓝 观察=灰 待确认=金 延续观察=青 终止=绿
 */

export const GRADE_META: Record<string, { color: string; raw: string; desc: string }> = {
  S: { color: "error", raw: "S", desc: "信号强度最高：S 级资本运作事件，最值得优先研究" },
  A: { color: "warning", raw: "A", desc: "信号强度较高：A 级资本运作事件" },
  B: { color: "processing", raw: "B", desc: "B 级事件：只在时间线挂载，不入候选池" },
  T: { color: "success", raw: "T", desc: "终止/撤回：事件已经终止，参与价值低" },
  观察: { color: "default", raw: "观察", desc: "因子命中充分但暂未达正式层级的候选" },
  待确认: { color: "gold", raw: "待确认", desc: "起涨点尚未确认（无二次上攻确认）" },
  延续观察: { color: "cyan", raw: "延续观察", desc: "确认起涨 41–120 交易日，延续机会" },
  无: { color: "default", raw: "无", desc: "暂无层级标注" },
};

export function GradeTag({ tier, grade }: { tier?: string | null; grade?: string | null }) {
  const key = (tier ?? grade ?? "") as string;
  const meta = GRADE_META[key] ?? GRADE_META["无"];
  return (
    <Tooltip title={meta.desc}>
      <Tag color="default" className={`grade-tag grade-tag--${key}`} style={{ marginInlineEnd: 0, fontWeight: 600 }}>
        {meta.raw}
      </Tag>
    </Tooltip>
  );
}

/** 终止状态（事件流程阶段/池状态）用绿色 Tag 表示“已撤”，与分层含义区分。 */
export function TerminatedTag({ status }: { status: string }) {
  const s = (status || "").trim();
  if (s === "终止" || s === "已终止" || s === "撤回" || s === "离场") {
    return (
      <Tag color="success" style={{ marginInlineEnd: 0 }}>
        {s}
      </Tag>
    );
  }
  return <span>{s || "-"}</span>;
}

export function StatusTag({ status }: { status: string }) {
  const s = (status || "").trim();
  const colors: Record<string, string> = {
    新入池: "blue",
    观察中: "default",
    重点跟踪: "gold",
    催化兑现中: "purple",
    已出信号: "magenta",
    归档: "default",
  };
  return <Tag color="default" className={`status-tag status-tag--${s}`}>{s}</Tag>;
}

export function LaneTag({ lane }: { lane: string | null }) {
  if (!lane) return <span>-</span>;
  const map: Record<string, string> = {
    新起涨: "red",
    延续观察: "cyan",
    待确认: "gold",
  };
  return (
    <Tag color="default" className={`lane-tag lane-tag--${lane}`} style={{ marginInlineEnd: 0 }}>
      {lane}
    </Tag>
  );
}

/** 命中项：统一人话解释，悬浮查看。 */
export const HIT_DESC: Record<string, string> = {
  涨幅: "起涨点以来累计涨幅已达标（机器确认的机械起点）",
  龙虎榜静默: "起涨后未上过龙虎榜，筹码被操纵的痕迹未被公开暴露",
  户数集中: "股东户数下降，散户离场筹码集中（利好）",
  弱信号共振: "多类弱信号（振幅/换手/偏离值等）在回看窗口内同现",
  小市值: "流通市值低于 A 级市值上限，更易被资金驱动",
};

export const HIT_COLOR: Record<string, string> = {
  涨幅: "red",
  龙虎榜静默: "blue",
  户数集中: "orange",
  弱信号共振: "purple",
  小市值: "cyan",
};

export function HitsTags({ hits }: { hits: string[] }) {
  if (!hits || hits.length === 0) return <span>-</span>;
  return (
    <span style={{ display: "inline-flex", flexWrap: "wrap", gap: 4 }}>
      {hits.map((h) => (
        <Tooltip key={h} title={HIT_DESC[h] ?? h}>
          <Tag color="default" className="hit-tag" style={{ marginInlineEnd: 0 }}>
            {h}
          </Tag>
        </Tooltip>
      ))}
    </span>
  );
}

export function MissingTags({ fields }: { fields: string[] }) {
  if (!fields || fields.length === 0) return <span>-</span>;
  const names: Record<string, string> = {
    holder_num: "股东户数",
    scan_result: "因子扫描",
    holder: "股东户数",
  };
  return (
    <Tooltip title={`缺失影响判断的数据：${fields.map((f) => names[f] ?? f).join("、")}`}>
      <Tag color="default" className="missing-tag">待补 {fields.length} 项</Tag>
    </Tooltip>
  );
}

// ---------- 数字格式化 ----------

/** 流通市值：后端可能返回「亿」或「元」，> 1e6 视为元并换算为亿。 */
export function fmtMv(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return "-";
  const yi = Math.abs(v) > 1e6 ? v / 1e8 : v;
  if (yi >= 1000) return yi.toFixed(0);
  return yi.toFixed(1);
}

/** 百分比数字（保留 1 位），null/missing 显示 "-" 且不参与情绪染色。 */
export function fmtPct(v: number | null | undefined, digits = 1): string {
  if (v == null || Number.isNaN(v)) return "-";
  return v.toFixed(digits);
}

/** 大数缩写：12.3 万 / 4.56 亿。 */
export function fmtCompact(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "-";
  const abs = Math.abs(v);
  if (abs >= 1e8) return (v / 1e8).toFixed(2) + " 亿";
  if (abs >= 1e4) return (v / 1e4).toFixed(1) + " 万";
  return String(Math.round(v));
}

/** 涨跌颜色（A股惯例：红涨绿跌）；null 不染色。 */
export function chgColor(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return "rgba(0,0,0,0.45)";
  if (v > 0) return "#cf1322";
  if (v < 0) return "#389e0d";
  return "rgba(0,0,0,0.45)";
}

/** 日期显示：截断为 YYYY-MM-DD。 */
export function fmtDate(s: string | null | undefined): string {
  if (!s) return "-";
  return s.length >= 10 ? s.slice(0, 10) : s;
}

/** 日期时间显示：YYYY-MM-DD HH:mm:ss。 */
export function fmtDt(s: string | null | undefined): string {
  if (!s) return "-";
  const d = s.slice(0, 19).replace("T", " ");
  return d;
}

/** 最新一条数据新鲜度：相对今天的天数，null = 未知。 */
export function freshnessDays(dateStr: string | null | undefined, today: string): number | null {
  if (!dateStr) return null;
  const t = new Date(today).getTime();
  const d = new Date(dateStr.slice(0, 10)).getTime();
  if (Number.isNaN(t) || Number.isNaN(d)) return null;
  return Math.round((t - d) / 86_400_000);
}

export function todayStr(): string {
  const d = new Date();
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}
