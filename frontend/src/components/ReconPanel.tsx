import { useEffect, useState } from "react";
import { Alert, Card, Empty, Select, Space, Spin, Typography } from "antd";
import { fetchRecon, fetchReconDates, ReconResp } from "../api";

export default function ReconPanel() {
  const [dates, setDates] = useState<string[]>([]);
  const [day, setDay] = useState("");
  const [data, setData] = useState<ReconResp | null>(null);
  const [err, setErr] = useState("");
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    fetchReconDates(30)
      .then((r) => {
        const ds = r.dates ?? [];
        setDates(ds);
        if (ds.length > 0) setDay(ds[0]);
      })
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    if (!day) return;
    let dead = false;
    setLoading(true);
    setErr("");
    fetchRecon(day)
      .then((r) => !dead && setData(r))
      .catch((e: unknown) => !dead && setErr(e instanceof Error ? e.message : String(e)))
      .finally(() => !dead && setLoading(false));
    return () => {
      dead = true;
    };
  }, [day]);

  const diffs = (data?.rows ?? []).filter((r) => r.only_cninfo.length > 0 || r.only_dc.length > 0);
  const clean = (data?.rows ?? []).length - diffs.length;

  return (
    <Card
      size="small"
      title="数据对账（巨潮 vs 东财 · 池内个股）"
      extra={
        <Select
          size="small"
          value={day || undefined}
          onChange={setDay}
          style={{ minWidth: 120 }}
          placeholder="选择日期"
          options={dates.map((d) => ({ value: d, label: d }))}
        />
      }
    >
      {err && <Alert type="error" showIcon message={err} />}
      {loading && (
        <Space>
          <Spin size="small" /> 对账中…
        </Space>
      )}
      {data && !loading && (
        <Space direction="vertical" size={10} style={{ display: "flex" }}>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            覆盖 {data.compared}/{data.universe} 只池内票；两源一致 {clean} 只，存在召回差异 {diffs.length} 只。
            标题已归一化（去公司前缀/空白），仅统计两源当日均有记录的天。
          </Typography.Text>
          {diffs.length === 0 ? (
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="当日无召回差异" />
          ) : (
            diffs.map((r) => (
              <div key={r.code}>
                <b className="num">{r.code}</b> {r.name || ""}
                {r.only_dc.length > 0 && (
                  <div style={{ color: "#fa8c16", fontSize: 12 }}>仅东财有（巨潮未见）：{r.only_dc.join("；")}</div>
                )}
                {r.only_cninfo.length > 0 && (
                  <div style={{ color: "#d4b106", fontSize: 12 }}>
                    仅巨潮有（东财漏召回，需人工复核）：{r.only_cninfo.join("；")}
                  </div>
                )}
              </div>
            ))
          )}
        </Space>
      )}
      {!data && !loading && !err && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="选择日期查看对账" />}
    </Card>
  );
}
