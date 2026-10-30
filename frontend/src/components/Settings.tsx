import { useEffect, useMemo, useState } from "react";
import {
  Alert,
  Button,
  Card,
  message,
  Slider,
  Space,
  Switch,
  Tag,
  Tooltip,
  Typography,
} from "antd";
import { QuestionCircleOutlined, SaveOutlined, UndoOutlined } from "@ant-design/icons";
import { ConfigResp, fetchConfig, saveConfig } from "../api";

/**
 * 参数说明：名字、单位、分组、短说明。全部滑块由 /api/config.bounds 生成，
 * 前端不维护参数白名单，新增参数自动出现。
 * INT_KEYS：后端要求整数的键（api.py SCAN_PARAM_INT_KEYS），滑块取整保证不 400。
 * LOG_KEYS：范围极宽的键（市值/成交额 0→10 万亿）使用对数刻度，小值区域也能精细调节。
 */

const INT_KEYS = new Set([
  "window",
  "min_list_days",
  "weak_days",
  "weak_min_types",
  "start_max_days",
  "start_s_days",
  "min_resonance",
  "archive_after_days",
  "archive_zombie_days",
  "archive_event_max_age_days",
]);

interface ParamMeta {
  name: string;
  unit: string;
  group: string;
  desc: string;
}

const PARAM_META: Record<string, ParamMeta> = {
  window: { name: "起涨回看窗口", unit: "个交易日", group: "扫描范围", desc: "全市场扫描的回看窗口：窗口内找最低点作为机械起点" },
  gain_min_pct: { name: "起涨最小涨幅", unit: "%", group: "扫描范围", desc: "从机械起涨点到现在，累计涨幅必须达标" },
  min_list_days: { name: "上市最少天数", unit: "天", group: "扫描范围", desc: "上市时间太短的次新股不入扫描" },
  min_avg_amount: { name: "窗口日均成交额下限", unit: "元", group: "扫描范围", desc: "太难买的票过滤（日均成交额低于此值出局）" },
  circ_mv_a_max: { name: "A 级市值上限", unit: "亿", group: "市值门槛", desc: "流通市值低于此值才可能被评为 A 级（小市值更易被驱动）" },
  circ_mv_watch_max: { name: "观察线市值上限", unit: "亿", group: "市值门槛", desc: "流通市值超过此值的票直接出局" },
  pool_mv_max: { name: "候选池市值上限", unit: "亿", group: "市值门槛", desc: "入池后市值超过此线上限会被归档（涨太多风险变大）" },
  holder_single_drop_pct: { name: "单期户数降幅阈值", unit: "%", group: "股东户数", desc: "最新一期股东户数环比降幅超过此值记入「户数集中」命中" },
  holder_cum_drop_pct: { name: "两期累计户数降幅阈值", unit: "%", group: "股东户数", desc: "连续两期户数累计降幅超过此值也记入命中" },
  weak_days: { name: "弱信号回看窗口", unit: "天", group: "弱信号", desc: "弱信号类型在窗口内出现即计数；同类型去重" },
  weak_min_types: { name: "弱信号最少命中类型数", unit: "类", group: "弱信号", desc: "窗口内至少命中几类弱信号才记「弱信号共振」" },
  start_max_days: { name: "新起涨最长时间", unit: "个交易日", group: "起涨与分层", desc: "确认起涨距今不超过此天数 = 「新起涨」路线" },
  continuation_max_days: { name: "延续观察最长时间", unit: "个交易日", group: "起涨与分层", desc: "新起涨之后、确认起涨不超过此天数 = 「延续观察」路线；再久则出局" },
  start_s_days: { name: "S 级新鲜度天数", unit: "个交易日", group: "起涨与分层", desc: "确认起涨距今不超过此天数的小市值+户数+弱信号三重共振才评为 S 级" },
  min_resonance: { name: "最少命中项数", unit: "项", group: "起涨与分层", desc: "命中项总数低于此阈值的票不入选榜单" },
  archive_after_days: { name: "完成事件静置归档天数", unit: "天", group: "归档管理", desc: "事件已完成/已发行后无新节点，静置此天数后归档" },
  archive_zombie_days: { name: "僵尸事件归档天数", unit: "天", group: "归档管理", desc: "事件进行中但无新节点推进，超过此天数视为僵尸归档" },
  archive_event_max_age_days: { name: "事件最大时长归档天数", unit: "天", group: "归档管理", desc: "事件从起始日算起超过此天数强制归档" },
};

const GROUP_ORDER = ["扫描范围", "市值门槛", "股东户数", "弱信号", "起涨与分层", "归档管理"];

/** 范围极宽、常用区集中在低端 → 对数刻度 */
function isLogKey(k: string): boolean {
  return k === "circ_mv_a_max" || k === "circ_mv_watch_max" || k === "pool_mv_max" || k === "min_avg_amount";
}

/** 显示值 → 滑块逻辑值 */
function toSlider(v: number, k: string): number {
  if (isLogKey(k)) {
    const safe = Math.max(v, 1e-6);
    return Math.log10(safe);
  }
  return v;
}

/** 滑块逻辑值 → 实际值 */
function fromSlider(sv: number, k: string): number {
  return isLogKey(k) ? Math.pow(10, sv) : sv;
}

function fmtSliderValue(v: number, key: string): string {
  if (key === "min_avg_amount") {
    if (v >= 1e8) return `${(v / 1e8).toFixed(v % 1e8 === 0 ? 0 : 1)} 亿`;
    if (v >= 1e4) return `${(v / 1e4).toFixed(0)} 万`;
    return String(v);
  }
  const meta = PARAM_META[key];
  const rounded = INT_KEYS.has(key) ? Math.round(v) : v;
  return `${rounded.toLocaleString("zh-CN", { maximumFractionDigits: 2 })}${meta?.unit ? ` ${meta.unit}` : ""}`;
}

export default function Settings() {
  const [cfg, setCfg] = useState<ConfigResp | null>(null);
  const [form, setForm] = useState<Record<string, number>>({});
  const [notify, setNotify] = useState(true);
  const [err, setErr] = useState("");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    fetchConfig()
      .then((c) => {
        setCfg(c);
        setNotify(c.notify_macos);
        setForm({ ...c.effective });
      })
      .catch((e: unknown) => setErr(e instanceof Error ? e.message : String(e)));
  }, []);

  const isChanged = useMemo(() => {
    if (!cfg) return false;
    for (const [k, v] of Object.entries(form)) {
      if (cfg.effective[k] != null && Math.abs(cfg.effective[k] - v) > 1e-9) return true;
    }
    return false;
  }, [form, cfg]);

  const keys = useMemo(() => Object.keys(cfg?.bounds ?? {}), [cfg]);

  const save = async () => {
    setSaving(true);
    setErr("");
    try {
      // 后端要求部分参数必须整数（window/min_list_days 等），拖动滑块会产生小数，保存前统一取整
      const params: Record<string, number> = {};
      for (const [k, v] of Object.entries(form)) {
        params[k] = INT_KEYS.has(k) ? Math.round(v) : v;
      }
      const next = await saveConfig({ scan_params: params, notify_macos: notify ? 1 : 0 });
      setCfg(next);
      setForm({ ...next.effective });
      message.success("设置已保存，下一轮扫描生效");
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  const restoreForm = () => {
    if (!cfg) return;
    setForm({ ...cfg.defaults });
    message.info("已恢复默认值，点【保存设置】才会真正生效");
  };

  if (err && !cfg) {
    return <Alert type="error" showIcon message="设置读取失败" description={err} />;
  }

  const byGroup = new Map<string, string[]>();
  for (const k of keys) {
    const g = PARAM_META[k]?.group ?? "其他";
    byGroup.set(g, [...(byGroup.get(g) ?? []), k]);
  }

  return (
    <Space direction="vertical" size={14} style={{ display: "flex" }}>
      <div className="page-summary settings-summary">
        <Space direction="vertical" size={10} style={{ display: "flex" }}>
          <Typography.Title level={5} style={{ margin: 0 }}>
            参数设置
          </Typography.Title>
          <Typography.Text type="secondary">
            拖动滑块修改扫描与榜单规则，点【保存设置】后下一轮扫描生效。恢复默认只重填表单，不直接改后端。
          </Typography.Text>
          {err && <Alert type="error" showIcon message={err} />}
        </Space>
      </div>

      <div className="settings-groups">
      {GROUP_ORDER.map((g) => (
        <Card
          key={g}
          className="settings-group"
          size="small"
          title={g}
          style={{ marginBottom: 0 }}
          extra={
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              {byGroup.get(g)?.length ?? 0} 项参数
            </Typography.Text>
          }
        >
          {byGroup.get(g)?.map((k) => {
            const meta = PARAM_META[k] ?? { name: k, unit: "", group: g, desc: "" };
            const b = cfg?.bounds[k];
            if (!b) return null;
            const [min, max] = b;
            const rawVal = form[k] ?? cfg!.defaults[k] ?? min;
            const val = INT_KEYS.has(k) ? Math.round(rawVal) : rawVal;
            const sMin = toSlider(min, k);
            const sMax = toSlider(max, k);
            const sVal = toSlider(val, k);
            const step = isLogKey(k) ? 0.02 : keyStep(k, min, max);
            const isNegative = min < 0;
            return (
              <div key={k} className="settings-field" style={{ marginBottom: 10 }}>
                <div
                  className="settings-row"
                  style={{
                    display: "grid",
                    gridTemplateColumns: "190px minmax(160px,1fr) 190px",
                    alignItems: "center",
                    gap: 8,
                  }}
                >
                  <Space size={6} style={{ whiteSpace: "nowrap" }}>
                    <Tooltip title={meta.desc}>
                      <QuestionCircleOutlined style={{ color: "#8ba0b3", fontSize: 12 }} />
                    </Tooltip>
                    <Typography.Text style={{ fontSize: 13 }}>{meta.name}</Typography.Text>
                  </Space>
                  <Slider
                    min={sMin}
                    max={sMax}
                    step={step}
                    value={sVal}
                    onChange={(sv) => {
                      let v = fromSlider(sv, k);
                      if (INT_KEYS.has(k)) v = Math.round(v);
                      setForm({ ...form, [k]: v });
                    }}
                    tooltip={{ formatter: (sv) => (sv == null ? "" : fmtSliderValue(fromSlider(sv, k), k)) }}
                    style={{ margin: 0 }}
                  />
                  <div className="settings-value" style={{ display: "flex", justifyContent: "flex-end", gap: 8, whiteSpace: "nowrap" }}>
                    <Typography.Text strong className="num" style={{ fontSize: 13 }}>
                      {isNegative && val > 0 ? `-${fmtSliderValue(val, k)}` : fmtSliderValue(val, k)}
                    </Typography.Text>
                    <Typography.Text type="secondary" style={{ fontSize: 11 }}>
                      {fmtRange(min, k)}~{fmtRange(max, k)}
                      {cfg && cfg.saved[k] != null && (
                        <span style={{ color: "#9c6c00" }}>· 已存 {fmtSliderValue(cfg.saved[k], k)}</span>
                      )}
                    </Typography.Text>
                  </div>
                </div>
              </div>
            );
          })}
        </Card>
      ))}
      </div>

      <Card size="small" title="通知" className="notification-card">
        <Space align="center" size={10}>
          <Switch checked={notify} onChange={setNotify} />
          <span>macOS 系统通知（仅当日新提醒，重要变化定向推送）</span>
        </Space>
      </Card>

      <div className="settings-actions">
        <Button type="primary" icon={<SaveOutlined />} loading={saving} onClick={save} disabled={!cfg}>
          保存设置
        </Button>
        <Button icon={<UndoOutlined />} onClick={restoreForm} disabled={!cfg}>
          恢复代码默认值
        </Button>
        {isChanged && <Tag color="gold">有未保存修改</Tag>}
      </div>
    </Space>
  );
}

function keyStep(k: string, min: number, max: number): number {
  const span = max - min;
  if (k === "min_avg_amount") return min >= 1e8 ? 1e7 : 1e6;
  if (span <= 10) return 1;
  if (span <= 100) return 5;
  if (span <= 500) return 10;
  if (span <= 2000) return 25;
  return 50;
}

function fmtRange(v: number, k: string): string {
  if (k === "min_avg_amount") {
    if (v >= 1e8) return `${(v / 1e8).toFixed(v % 1e8 === 0 ? 0 : 1)} 亿`;
    if (v >= 1e4) return `${(v / 1e4).toFixed(0)} 万`;
    return String(v);
  }
  return v.toLocaleString("zh-CN", { maximumFractionDigits: 2 });
}
