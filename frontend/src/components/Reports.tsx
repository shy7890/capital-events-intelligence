import { useCallback, useEffect, useState } from "react";
import {
  Alert,
  Badge,
  Button,
  Card,
  Collapse,
  Empty,
  List,
  Space,
  Tag,
  Tooltip,
  Typography,
} from "antd";
import {
  CheckCircleTwoTone,
  CloseCircleTwoTone,
  ReloadOutlined,
  SyncOutlined,
} from "@ant-design/icons";
import ReconPanel from "./ReconPanel";
import {
  fetchDailyReport,
  fetchHealth,
  fetchNotifications,
  fetchPool,
  DailyReport,
  HealthResp,
  markNotificationsRead,
  NotificationRow,
} from "../api";

const REFRESH_MS = 60_000;

function levelColor(level: string): string {
  const l = (level || "").toLowerCase();
  if (l === "error" || l === "红" || l === "red") return "error";
  if (l === "warn" || l === "warning" || l === "橙") return "warning";
  if (l === "ok" || l === "green" || l === "info") return "success";
  return "default";
}

/** 今日数据更新状态（人话：正常低调，失败才醒目） */
function RefreshState({ report }: { report: DailyReport | null }) {
  if (!report?.market_refresh) return null;
  const mr = report.market_refresh;
  const items: { title: string; status: string; desc: string }[] = [];

  const klineOk = (mr.kline_total ?? 0) > 0 && (mr.kline_failed ?? 0) === 0;
  items.push({
    title: "K 线数据",
    status: klineOk ? "ok" : mr.kline_pending > 0 ? "running" : mr.kline_failed > 0 ? "fail" : "idle",
    desc: klineOk
      ? `已完成 ${mr.kline_success} 只` +
        (mr.kline_skipped > 0 ? `，跳过 ${mr.kline_skipped} 只` : "")
      : mr.kline_pending > 0
        ? `进行中（已完成 ${mr.kline_success} / ${mr.kline_total}）`
        : mr.kline_failed > 0
          ? `失败 ${mr.kline_failed} 只，榜单可能延迟`
          : "今日尚未运行（晚间 17:30 后自动开始）",
  });

  const lh = (mr.longhu_status ?? "").toLowerCase();
  items.push({
    title: "龙虎榜数据",
    status: lh === "done" || lh === "ok" ? "ok" : lh.includes("fail") || lh.includes("err") ? "fail" : lh.includes("run") || lh.includes("pend") ? "running" : "idle",
    desc: lh === "done" || lh === "ok"
      ? "已完成"
      : lh.includes("fail") || lh.includes("err")
        ? "采集失败，今日榜单数据可能不完整"
        : lh.includes("run") || lh.includes("pend")
          ? "进行中"
          : "今日尚未运行",
  });

  const hasFail = items.some((i) => i.status === "fail");
  return (
    <Alert
      className="refresh-state"
      type={hasFail ? "error" : "info"}
      showIcon
      message={hasFail ? "今日自动更新有失败项" : "今日数据更新状态"}
      description={
        <Space direction="vertical" size={4} style={{ display: "flex" }}>
          {items.map((i) => {
            const icon =
              i.status === "ok" ? (
                <CheckCircleTwoTone twoToneColor="#389e0d" />
              ) : i.status === "fail" ? (
                <CloseCircleTwoTone twoToneColor="#cf1322" />
              ) : i.status === "running" ? (
                <SyncOutlined spin style={{ color: "#1677ff" }} />
              ) : (
                <Badge status="default" />
              );
            return (
              <Space key={i.title} className={`data-status-row data-status-row--${i.status}`} size={6}>
                {icon}
                <b>{i.title}</b>
                <Typography.Text type={i.status === "fail" ? "danger" : "secondary"}>
                  {i.desc}
                </Typography.Text>
              </Space>
            );
          })}
          {mr.blocking && (
            <Typography.Text type="danger">扫描被阻断:{mr.blocking_reason ?? "未知原因"}</Typography.Text>
          )}
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            页面每 60 秒自动检查显示；也可以点右上角「立即刷新」。
          </Typography.Text>
        </Space>
      }
    />
  );
}

export default function Reports() {
  const [health, setHealth] = useState<HealthResp | null>(null);
  const [notifs, setNotifs] = useState<NotificationRow[]>([]);
  const [report, setReport] = useState<DailyReport | null>(null);
  const [poolTotal, setPoolTotal] = useState(0);
  const [err, setErr] = useState("");
  const [lastRefresh, setLastRefresh] = useState("");
  const [tabVisible, setTabVisible] = useState(true);

  const refresh = useCallback(() => {
    Promise.allSettled([
      fetchHealth(),
      fetchNotifications(false, 80),
      fetchDailyReport(),
      fetchPool({ limit: 1 }),
    ]).then(([h, n, d, p]) => {
      if (h.status === "fulfilled") setHealth(h.value);
      if (n.status === "fulfilled") setNotifs(n.value.rows ?? []);
      if (d.status === "fulfilled") setReport(d.value);
      if (p.status === "fulfilled") setPoolTotal(p.value.total ?? 0);
      const firstErr = [h, n, d, p].find((r) => r.status === "rejected");
      setErr(firstErr ? String((firstErr as PromiseRejectedResult).reason?.message ?? "") : "");
      setLastRefresh(new Date().toLocaleTimeString("zh-CN", { hour12: false }));
    });
  }, []);

  useEffect(() => {
    refresh();
    const t = setInterval(() => {
      if (document.visibilityState === "visible") refresh();
    }, REFRESH_MS);
    const onVis = () => setTabVisible(document.visibilityState === "visible");
    document.addEventListener("visibilitychange", onVis);
    return () => {
      clearInterval(t);
      document.removeEventListener("visibilitychange", onVis);
    };
  }, [refresh]);

  const markRead = async () => {
    try {
      await markNotificationsRead();
      refresh();
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  };

  const healthOk = health && health.status === "ok" && health.db === "ok";
  const unreadCount = notifs.filter((n) => !n.is_read).length;

  return (
    <Space direction="vertical" size={14} style={{ display: "flex" }}>
      <Card size="small" className="service-overview">
        <Space style={{ width: "100%", justifyContent: "space-between", flexWrap: "wrap" }}>
          <Space size={10}>
            <Badge status={healthOk ? "success" : "error"} />
            <b>{healthOk ? "处理服务与数据库运行正常" : "服务异常"}</b>
          </Space>
          <Space size={8}>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              每 60s 自动刷新 · 上次 {lastRefresh || "-"}
            </Typography.Text>
            <Button size="small" icon={<ReloadOutlined />} onClick={refresh}>
              立即刷新
            </Button>
          </Space>
        </Space>
        {health && <Collapse ghost items={[{ key: "technical", label: "技术详情", children: <Typography.Text type="secondary" className="num" style={{ fontSize: 12 }}>status={health.status} · db={health.db} · tables={health.tables}</Typography.Text> }]} />}
      </Card>

      {err && <Alert type="error" showIcon message={err} />}

      <RefreshState report={report} />

      <Card size="small" className="data-scale-card">
        <Space wrap size={24}>
          <Typography.Title level={5} style={{ margin: 0 }}>
            数据规模
          </Typography.Title>
          <Space size={16}>
            <span>
              事件总数 <b className="num">{report?.events_total ?? "-"}</b>
            </span>
            <span>
              候选池 <b className="num">{poolTotal || "-"}</b>
            </span>
            <Tooltip title="今日运行阶段明细">
              <span>
                今日阶段 <b className="num">{report?.today_stages.length ?? 0}</b> 步
              </span>
            </Tooltip>
          </Space>
        </Space>
        {report && Object.keys(report.pool_by_status ?? {}).length > 0 && (
          <Space size={6} wrap style={{ marginTop: 8 }}>
            {Object.entries(report.pool_by_status).map(([s, n]) => (
              <Tag key={s} color={s === "归档" ? "default" : "blue"}>
                {s} {n}
              </Tag>
            ))}
          </Space>
        )}
      </Card>

      <Card
        size="small"
        title={
          <Space size={8}>
            通知中心
            <Badge count={unreadCount} size="small" />
          </Space>
        }
        extra={
          <Button size="small" type="primary" onClick={markRead} disabled={unreadCount === 0}>
            全部标记已读
          </Button>
        }
      >
        {notifs.length === 0 ? (
          <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无通知" />
        ) : (
          <List
            size="small"
            dataSource={notifs}
            style={{ maxHeight: 480, overflowY: "auto" }}
            renderItem={(n) => (
              <List.Item extra={<Tag>{n.ts}</Tag>}>
                <List.Item.Meta
                  title={
                    <Space size={6}>
                      {!n.is_read && <Badge status="processing" />}
                      <Tag color={levelColor(n.level)} style={{ marginInlineEnd: 0 }}>
                        {n.level}
                      </Tag>
                      <b>{n.title}</b>
                    </Space>
                  }
                  description={
                    <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                      {n.body}
                    </Typography.Text>
                  }
                />
              </List.Item>
            )}
          />
        )}
      </Card>

      <Card size="small">
        <Collapse
          ghost
          items={[
            {
              key: "report",
              label: (
                <Space size={8}>
                  每日报告
                  <Tooltip title="新入池 / 节点推进 / 离场信号 / 扫描异常的数量概览">
                    <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                      盘后批任务产出
                    </Typography.Text>
                  </Tooltip>
                </Space>
              ),
              children:
                (report?.today_stages ?? []).length === 0 ? (
                  <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="今日暂无批任务记录（17:30 后自动开始）" />
                ) : (
                  <Space direction="vertical" size={8} style={{ display: "flex" }}>
                    {report!.today_stages.map((s, i) => {
                      const red = (s.status || "").toLowerCase() === "red" || (s.status || "").toLowerCase().includes("fail");
                      return (
                        <Space key={i} size={8}>
                          {red ? (
                            <CloseCircleTwoTone twoToneColor="#cf1322" />
                          ) : (
                            <CheckCircleTwoTone twoToneColor="#389e0d" />
                          )}
                          <b>{s.stage}</b>
                          <Typography.Text type={red ? "danger" : "secondary"}>
                            {red ? "运行失败" : "已完成"}
                          </Typography.Text>
                          {s.stats && Object.keys(s.stats).length > 0 && (
                            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                              {JSON.stringify(s.stats).slice(0, 120)}
                            </Typography.Text>
                          )}
                        </Space>
                      );
                    })}
                  </Space>
                ),
            },
          ]}
        />
      </Card>

      <ReconPanel />
    </Space>
  );
}
