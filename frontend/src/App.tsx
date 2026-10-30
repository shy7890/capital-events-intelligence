import { useEffect, useMemo, useState } from "react";
import { Badge, AutoComplete, Button, Input, Layout, Menu, Space, Tag, Tooltip, Typography } from "antd";
import {
  BellOutlined,
  CompressOutlined,
  FireOutlined,
  HomeOutlined,
  LineChartOutlined,
  SearchOutlined,
  SettingOutlined,
  StarOutlined,
  SwitcherOutlined,
} from "@ant-design/icons";
import { fetchPool, fetchNotifications, fetchHealth, PoolRow } from "./api";
import { CompareProvider } from "./components/comparectx";
import Today from "./components/Today";
import Opportunities from "./components/Opportunities";
import Workbench from "./components/Workbench";
import Watchlist from "./components/Watchlist";
import Compare from "./components/Compare";
import Reports from "./components/Reports";
import Settings from "./components/Settings";

type Page =
  | "today"
  | "scan"
  | "workbench"
  | "watchlist"
  | "compare"
  | "reports"
  | "settings";

const PAGE_TITLES: Record<Page, string> = {
  today: "今日值得看",
  scan: "因子机会",
  workbench: "单票研究",
  watchlist: "我的跟踪",
  compare: "多票对比",
  reports: "运行与提醒",
  settings: "设置",
};

const MENU_ITEMS = [
  {
    type: "group" as const,
    label: "核心",
    children: [
      { key: "today", icon: <HomeOutlined />, label: "今日值得看" },
      { key: "scan", icon: <FireOutlined />, label: "因子机会" },
      { key: "workbench", icon: <LineChartOutlined />, label: "单票研究" },
      { key: "watchlist", icon: <StarOutlined />, label: "我的跟踪" },
    ],
  },
  {
    type: "group" as const,
    label: "分析",
    children: [
      { key: "compare", icon: <CompressOutlined />, label: "多票对比" },
    ],
  },
  {
    type: "group" as const,
    label: "系统",
    children: [
      { key: "reports", icon: <BellOutlined />, label: "运行与提醒" },
      { key: "settings", icon: <SettingOutlined />, label: "设置" },
    ],
  },
];

function parsePage(): { page: Page; code: string } {
  const qs = new URLSearchParams(window.location.search);
  const p = qs.get("page") as Page | null;
  const valid: Page[] = ["today", "scan", "workbench", "watchlist", "compare", "reports", "settings"];
  return {
    page: p && valid.includes(p) ? p : "today",
    code: qs.get("code") ?? "",
  };
}

export default function App() {
  const [init] = useState(parsePage);
  const [page, setPage] = useState<Page>(init.page);
  const [code, setCode] = useState<string>(init.code);
  const [collapsed, setCollapsed] = useState(false);
  const [kw, setKw] = useState("");
  const [cand, setCand] = useState<PoolRow[]>([]);
  const [unread, setUnread] = useState(0);
  const [healthOk, setHealthOk] = useState(true);

  const navScrollKey = (p: string) => `zhuang.scroll.${p}`;

  const savePageScroll = (p: Page) => {
    try {
      sessionStorage.setItem(navScrollKey(p), String(window.scrollY));
    } catch {
      /* ignore */
    }
  };

  const consumePageScroll = (p: Page): number | null => {
    try {
      const raw = sessionStorage.getItem(navScrollKey(p));
      sessionStorage.removeItem(navScrollKey(p));
      if (raw == null) return null;
      const y = Number(raw);
      return Number.isFinite(y) ? y : null;
    } catch {
      return null;
    }
  };

  const syncUrl = (p: Page, c: string) => {
    const qs = new URLSearchParams();
    qs.set("page", p);
    if (c) qs.set("code", c);
    const next = `${window.location.pathname}?${qs.toString()}`;
    if (window.location.href !== next) window.history.pushState({ page: p, code: c }, "", next);
  };

  const navigate = (p: Page, c = "") => {
    if (p !== page) savePageScroll(page);
    setPage(p);
    setCode(c);
    syncUrl(p, c);
  };

  const openWorkbench = (c: string) => {
    // 记录来源页，供工作台“返回列表”使用
    if (page !== "workbench") {
      try {
        sessionStorage.setItem("zhuang.wb.from", page);
      } catch {
        /* ignore */
      }
    }
    navigate("workbench", c);
  };

  useEffect(() => {
    const onPop = () => {
      const { page: p, code: c } = parsePage();
      savePageScroll(page);
      setPage(p);
      setCode(c);
    };
    window.addEventListener("popstate", onPop);
    fetchHealth()
      .then((h) => setHealthOk(h.status === "ok" && h.db === "ok"))
      .catch(() => setHealthOk(false));
    fetchNotifications(true, 1)
      .then((r) => setUnread(r.rows?.length ?? 0))
      .catch(() => undefined);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  // 页面切换后恢复上次滚动位置（如：单票研究 → 返回来源页时列表停在原处）
  useEffect(() => {
    const y = consumePageScroll(page);
    if (y == null) return;
    const t = setTimeout(() => window.scrollTo(0, y), 0);
    return () => clearTimeout(t);
  }, [page]);

  useEffect(() => {
    if (!kw.trim()) {
      setCand([]);
      return;
    }
    const t = setTimeout(() => {
      fetchPool({ q: kw.trim(), limit: 8 })
        .then((r) => setCand(r.rows ?? []))
        .catch(() => setCand([]));
    }, 250);
    return () => clearTimeout(t);
  }, [kw]);

  const searchOptions = useMemo(
    () =>
      cand.map((r) => ({
        value: r.code,
        label: (
          <Space size={6}>
            <Typography.Text strong>{r.name}</Typography.Text>
            <Typography.Text type="secondary">{r.code}</Typography.Text>
            <Tag style={{ marginInlineEnd: 0 }}>{r.event_type || "池内"}</Tag>
          </Space>
        ),
      })),
    [cand],
  );

  return (
    <CompareProvider>
      <Layout className="app-shell" style={{ minHeight: "100vh" }}>
        <Layout.Sider
          theme="light"
        collapsible
        collapsed={collapsed}
        onCollapse={setCollapsed}
        width={216}
        breakpoint="lg"
        className="app-sider"
        style={{ borderRight: "1px solid #DCE3EC" }}
      >
        <div
          className="app-brand"
          style={{
            height: 56,
            display: "flex",
            alignItems: "center",
            justifyContent: collapsed ? "center" : "flex-start",
            padding: collapsed ? 0 : "0 16px",
            gap: 8,
          }}
        >
          <SwitcherOutlined style={{ fontSize: 20, color: "#1D5FA7" }} />
          {!collapsed && (
            <span>
              <b style={{ fontSize: 15, letterSpacing: 1 }}>资本事件研究台</b>
              <Typography.Text type="secondary" style={{ fontSize: 11, marginLeft: 4 }}>
                Capital Events Intelligence
              </Typography.Text>
            </span>
          )}
        </div>
        <Menu
          mode="inline"
          items={MENU_ITEMS}
          selectedKeys={[page === "workbench" && code ? "workbench" : page]}
          onClick={(e) => navigate(e.key as Page)}
        />
      </Layout.Sider>

      <Layout className="app-main">
        <Layout.Header
          className="app-header"
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            padding: "0 20px",
            borderBottom: "1px solid #DCE3EC",
            height: 56,
            position: "sticky",
            top: 0,
            zIndex: 100,
          }}
        >
          <Space size={12} className="app-header-title">
            <Typography.Title
              level={5}
              style={{ margin: 0, whiteSpace: "nowrap", maxWidth: 180, overflow: "hidden", textOverflow: "ellipsis" }}
            >
              {PAGE_TITLES[page]}
            </Typography.Title>
            {page === "workbench" && code && (
              <Typography.Text type="secondary">{code}</Typography.Text>
            )}
          </Space>
          <Space size={12} className="app-header-tools">
            <AutoComplete
              style={{ width: 260, maxWidth: "38vw" }}
              options={searchOptions}
              value={kw}
              onChange={setKw}
              onSelect={(c) => {
                openWorkbench(c);
                setKw("");
              }}
              onKeyDown={(e) => {
                if (e.key === "Enter" && /^\d{6}$/.test(kw.trim())) {
                  openWorkbench(kw.trim());
                  setKw("");
                }
              }}
            >
              <Input
                allowClear
                prefix={<SearchOutlined />}
                placeholder="搜索代码 / 名称，如 123456 或示例公司"
                size="middle"
              />
            </AutoComplete>
            <Tooltip title="未读提醒">
              <Badge count={unread} size="small">
                <Button
                  type="text"
                  icon={<BellOutlined />}
                  style={{ fontSize: 16 }}
                  onClick={() => navigate("reports")}
                />
              </Badge>
            </Tooltip>
            <Tooltip title={healthOk ? "数据服务正常" : "数据服务异常，点此查看详情"}>
              <Badge status={healthOk ? "success" : "error"} text={healthOk ? "数据正常" : "数据异常"} />
            </Tooltip>
          </Space>
        </Layout.Header>

        <Layout.Content className="app-content" style={{ padding: 24, maxWidth: 1500, width: "100%", margin: "0 auto" }}>
          {/* 常驻渲染切换：返回来源页时 Today/Opportunities 从未卸载，筛选/页码天然保留 */}
          <div style={{ display: page === "today" ? undefined : "none" }}>
            <Today onOpen={openWorkbench} />
          </div>
          <div style={{ display: page === "scan" ? undefined : "none" }}>
            <Opportunities onOpen={openWorkbench} />
          </div>
          {page === "workbench" && (
            <Workbench
              code={code}
              onCodeChange={(c) => navigate("workbench", c)}
              onBack={() => {
                let from = "today";
                try {
                  from = sessionStorage.getItem("zhuang.wb.from") ?? "today";
                } catch {
                  /* ignore */
                }
                navigate(from as Page);
              }}
              backLabel="返回来源页"
            />
          )}
          {page === "watchlist" && <Watchlist onOpen={openWorkbench} />}
          {page === "compare" && <Compare />}
          {page === "reports" && <Reports />}
          {page === "settings" && <Settings />}
        </Layout.Content>
      </Layout>
      </Layout>
    </CompareProvider>
  );
}
