// 集中类型定义与 fetch 封装。所有请求走相对路径 /api/...，
// 网络/解析错误统一抛出「数据服务未就绪，请先启动后端」。

export interface PoolRow {
  code: string;
  name: string;
  board: string;
  circ_mv: number | null;
  grade: string; // S/A/B/T
  status: string; // 池状态
  event_type: string; // 最新事件类型
  event_status: string; // 进行中/已完成/已发行/终止
  start_date: string; // 事件起始日
  resonance_count: number;
  exit_signals: string[];
  note: string;
  entered_at: string;
}

export interface PoolResp {
  total: number;
  rows: PoolRow[];
}

export interface EventNode {
  node_type: string;
  node_date: string;
  title: string;
  pdf_url?: string | null;
}

export interface EventDetail {
  id: number;
  event_type: string;
  grade: string;
  start_date: string;
  status: string;
  nodes: EventNode[];
}

export interface EventsResp {
  events: EventDetail[];
}

export interface KlineRow {
  trade_date: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  amount: number;
  turnover: number;
  close_qfq: number;
}

export interface KlineResp {
  code: string;
  name: string;
  rows: KlineRow[];
}

export interface HolderRow {
  disclose_date: string;
  holder_num: number;
  pct_change: number | null;
}

export interface HolderResp {
  rows: HolderRow[];
}

export interface NoteRow {
  id: number;
  code: string;
  trade_date: string;
  content: string;
  tags: string[] | string | null;
  created_at: string;
}

export interface NotesResp {
  rows: NoteRow[];
}

export interface NotificationRow {
  id: number;
  ts: string;
  level: string; // info/warn/error 等
  title: string;
  body: string;
  is_read: number | boolean;
}

export interface NotificationsResp {
  rows: NotificationRow[];
}

export interface HealthResp {
  status: string;
  db: string;
  tables: number;
}

const NET_ERR = "数据服务未就绪，请先启动后端";

async function req<T>(url: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(url, init);
  } catch {
    throw new Error(NET_ERR);
  }
  if (!res.ok) {
    // 5xx/4xx 也视为服务不可用（后端未就绪或接口异常）
    throw new Error(res.status >= 500 ? NET_ERR : `请求失败（HTTP ${res.status}）`);
  }
  try {
    return (await res.json()) as T;
  } catch {
    throw new Error(NET_ERR);
  }
}

export interface PoolQuery {
  status?: string;
  grade?: string;
  q?: string;
  mv_max?: number;
  include_archived?: boolean;
  event_kind?: string; // 事件属性：重组/定增/控制权
  hide_reviewed?: boolean; // 隐藏已评级（已看过）
  reviewed?: "reviewed" | "unreviewed"; // 看过状态筛选
  limit?: number;
  offset?: number;
}

export function fetchPool(p: PoolQuery = {}): Promise<PoolResp> {
  const sp = new URLSearchParams();
  if (p.status) sp.set("status", p.status);
  if (p.grade) sp.set("grade", p.grade);
  if (p.q) sp.set("q", p.q);
  if (p.mv_max != null) sp.set("mv_max", String(p.mv_max));
  if (p.include_archived) sp.set("include_archived", "1");
  if (p.event_kind) sp.set("event_kind", p.event_kind);
  if (p.reviewed) sp.set("reviewed", p.reviewed);
  else if (p.hide_reviewed) sp.set("hide_reviewed", "1");
  sp.set("limit", String(p.limit ?? 50));
  sp.set("offset", String(p.offset ?? 0));
  return req<PoolResp>(`/api/pool?${sp.toString()}`);
}

export function fetchEvents(code: string): Promise<EventsResp> {
  return req<EventsResp>(`/api/events/${encodeURIComponent(code)}`);
}

export function fetchKline(code: string, start?: string, end?: string): Promise<KlineResp> {
  const sp = new URLSearchParams();
  if (start) sp.set("start", start);
  if (end) sp.set("end", end);
  const qs = sp.toString();
  return req<KlineResp>(`/api/kline/${encodeURIComponent(code)}${qs ? `?${qs}` : ""}`);
}

export function fetchHolder(code: string): Promise<HolderResp> {
  return req<HolderResp>(`/api/holder/${encodeURIComponent(code)}`);
}

export function fetchNotes(code: string): Promise<NotesResp> {
  return req<NotesResp>(`/api/notes/${encodeURIComponent(code)}`);
}

export function createNote(body: {
  code: string;
  content: string;
  tags: string[];
  trade_date: string;
}): Promise<{ id: number }> {
  return req<{ id: number }>("/api/notes", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export function fetchNotifications(unread = true, limit = 50): Promise<NotificationsResp> {
  const sp = new URLSearchParams();
  sp.set("limit", String(limit));
  if (unread) sp.set("unread", "1");
  return req<NotificationsResp>(`/api/notifications?${sp.toString()}`);
}

export function markNotificationsRead(): Promise<{ updated: number }> {
  return req<{ updated: number }>("/api/notifications/read", { method: "POST" });
}

export function fetchHealth(): Promise<HealthResp> {
  return req<HealthResp>("/api/health");
}

export interface ReportStage {
  stage: string;
  status: string;
  stats: Record<string, unknown> | null;
}

export interface MarketRefresh {
  run_date: string | null;
  status: string;
  kline_total: number;
  kline_success: number;
  kline_skipped: number;
  kline_failed: number;
  kline_pending: number;
  longhu_status: string;
  blocking: boolean;
  blocking_reason: string | null;
}

export interface DailyReport {
  date: string;
  today_stages: ReportStage[];
  pool_by_status: Record<string, number>;
  events_total: number;
  pool_total: number;
  market_refresh: MarketRefresh;
  recent_notifications: NotificationRow[];
}

export function fetchDailyReport(): Promise<DailyReport> {
  return req<DailyReport>("/api/report/daily");
}

// ---------- M4 流水线 B：疑似庄股榜 ----------

export interface ScanRow {
  code: string;
  name: string | null;
  board: string | null;
  circ_mv: number | null;
  gain_pct: number | null;
  holder_drop_pct: number | null;
  resonance: number;
  tier: string | null; // S / A / B / 观察 / 待确认 / 延续观察
  hits: string[];
  start_date: string | null;
  start_age_days: number | null;
  lane: string | null; // 路线：新起涨 / 延续观察 / 待确认
  mechanical_start_date: string | null;
  confirmed_start_date: string | null;
  missing_fields: string[];
  in_pool: boolean;
}

export interface ScanResp {
  date: string | null;
  total: number;
  rows: ScanRow[];
}

export interface ScanSummary {
  latest_date: string | null;
  universe: number;
  hits: number;
  tier_counts: Record<string, number>;
}

export function fetchScan(p: { date?: string; limit?: number; offset?: number } = {}): Promise<ScanResp> {
  const sp = new URLSearchParams();
  if (p.date) sp.set("date", p.date);
  sp.set("limit", String(p.limit ?? 100));
  sp.set("offset", String(p.offset ?? 0));
  return req<ScanResp>(`/api/scan?${sp.toString()}`);
}

export function fetchScanByDate(date: string, limit = 500): Promise<ScanResp> {
  return fetchScan({ date, limit });
}

export function fetchScanSummary(): Promise<ScanSummary> {
  return req<ScanSummary>("/api/scan/summary");
}

// ---------- 我的跟踪（M6c 后端 /api/watchlist） ----------

export interface WatchlistChange {
  type: string;
  date: string;
  actionable: boolean;
  title: string;
  detail: string;
}

export interface WatchlistFreshness {
  stock: string | null;
  scan: string | null;
  kline: string | null;
  holder: string | null;
  longhu: string | null;
  announcement: string | null;
}

export interface WatchlistRow {
  code: string;
  name: string | null;
  board: string | null;
  status: "active" | "history";
  note: string | null;
  added_at: string | null;
  updated_at: string | null;
  removed_at: string | null;
  lane: string | null;
  tier: string | null;
  scan_date: string | null;
  start_date: string | null;
  start_age_days: number | null;
  mechanical_start_date: string | null;
  confirmed_start_date: string | null;
  missing_fields: string[];
  freshness: WatchlistFreshness | null;
  price_change: { date: string; close: number; pct: number } | null;
  changes: WatchlistChange[];
}

export interface WatchlistResp {
  total: number;
  rows: WatchlistRow[];
}

export function fetchWatchlist(includeHistory = false, limit = 200): Promise<WatchlistResp> {
  const sp = new URLSearchParams();
  if (includeHistory) sp.set("include_history", "1");
  sp.set("limit", String(limit));
  return req<WatchlistResp>(`/api/watchlist?${sp.toString()}`);
}

export function addWatchlist(code: string, note?: string): Promise<WatchlistRow> {
  return req<WatchlistRow>("/api/watchlist", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ code, note }),
  });
}

export function updateWatchlist(
  code: string,
  body: { note?: string; status?: "active" | "history" },
): Promise<WatchlistRow> {
  return req<WatchlistRow>(`/api/watchlist/${encodeURIComponent(code)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export function removeWatchlist(code: string): Promise<WatchlistRow> {
  return req<WatchlistRow>(`/api/watchlist/${encodeURIComponent(code)}`, { method: "DELETE" });
}

// ---------- M6 配置管理（PRD F10） ----------

export interface ConfigResp {
  defaults: Record<string, number>;
  saved: Record<string, number>;
  effective: Record<string, number>;
  notify_macos: boolean;
  bounds: Record<string, [number, number]>;
}

export function fetchConfig(): Promise<ConfigResp> {
  return req<ConfigResp>("/api/config");
}

export function saveConfig(body: {
  scan_params?: Record<string, number>;
  notify_macos?: 0 | 1;
}): Promise<ConfigResp> {
  return req<ConfigResp>("/api/config", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

// ---------- M6 数据对账（PRD §7） ----------

export interface ReconRow {
  code: string;
  name: string | null;
  only_cninfo: string[];
  only_dc: string[];
  both: number;
}

export interface ReconResp {
  date: string | null;
  universe: number;
  compared: number;
  rows: ReconRow[];
}

export function fetchReconDates(limit = 30): Promise<{ dates: string[] }> {
  return req<{ dates: string[] }>(`/api/recon/dates?limit=${limit}`);
}

export function fetchRecon(day?: string): Promise<ReconResp> {
  const qs = day ? `?day=${encodeURIComponent(day)}` : "";
  return req<ReconResp>(`/api/recon${qs}`);
}
