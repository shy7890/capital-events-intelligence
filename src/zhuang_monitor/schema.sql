-- 资本事件研究台 SQLite schema
-- 全部使用 IF NOT EXISTS，保证脚本可重复执行（幂等）。

CREATE TABLE IF NOT EXISTS stock (
    code         TEXT PRIMARY KEY,          -- 6 位代码
    name         TEXT NOT NULL,
    board        TEXT,                      -- 沪市主板/深市主板/创业板/科创板
    list_date    TEXT,                      -- 上市日 YYYY-MM-DD
    is_st        INTEGER NOT NULL DEFAULT 0,
    total_mv     REAL,                      -- 总市值（亿元）
    circ_mv      REAL,                      -- 流通市值（亿元）
    updated_at   TEXT
);

CREATE TABLE IF NOT EXISTS announcement (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    code        TEXT NOT NULL,
    pub_date    TEXT NOT NULL,              -- 公告日 YYYY-MM-DD
    title       TEXT NOT NULL,
    pdf_url     TEXT,
    category    TEXT,                       -- 巨潮 category 代码
    source      TEXT NOT NULL,              -- cninfo/ifind/akshare/...
    fetched_at  TEXT NOT NULL,
    UNIQUE(code, pub_date, title)
);
CREATE INDEX IF NOT EXISTS idx_announcement_code_date ON announcement(code, pub_date);
CREATE INDEX IF NOT EXISTS idx_announcement_pub_date ON announcement(pub_date);

-- 弱信号独立留痕：即使没有进行中的资本运作事件，也保留原始命中供因子扫描和复盘。
CREATE TABLE IF NOT EXISTS weak_signal (
    code            TEXT NOT NULL,
    signal_type     TEXT NOT NULL,
    signal_date     TEXT NOT NULL,
    announcement_id INTEGER NOT NULL DEFAULT 0,
    title           TEXT,
    source          TEXT,
    created_at      TEXT NOT NULL,
    UNIQUE(code, signal_type, signal_date, announcement_id)
);
CREATE INDEX IF NOT EXISTS idx_weak_signal_code_date
    ON weak_signal(code, signal_date, signal_type);

CREATE TABLE IF NOT EXISTS event (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    code                  TEXT NOT NULL,
    event_type            TEXT NOT NULL,    -- 定增/重组/控制权变更/...
    grade                 TEXT NOT NULL,    -- S/A/B/T(终止)
    start_date            TEXT NOT NULL,    -- 最初公告日
    status                TEXT NOT NULL DEFAULT '进行中',
    first_announcement_id INTEGER,
    created_at            TEXT NOT NULL,
    updated_at            TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_event_code ON event(code);

CREATE TABLE IF NOT EXISTS flow_node (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id         INTEGER NOT NULL REFERENCES event(id),
    node_type        TEXT NOT NULL,         -- 筹划/停牌/方案披露/股东大会/受理/问询/过会/批文/发行/过户/解禁/终止
    node_date        TEXT NOT NULL,
    announcement_id  INTEGER REFERENCES announcement(id),
    remark           TEXT,
    created_at       TEXT NOT NULL,
    UNIQUE(event_id, node_type, node_date)
);

CREATE TABLE IF NOT EXISTS pool_entry (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    code            TEXT NOT NULL,
    event_id        INTEGER REFERENCES event(id),
    status          TEXT NOT NULL,          -- 新入池/观察中/重点跟踪/催化兑现中/已出信号/离场/归档
    grade           TEXT NOT NULL,
    entered_at      TEXT NOT NULL,
    resonance_count INTEGER NOT NULL DEFAULT 0,
    exit_signals    TEXT,                   -- JSON 数组
    note            TEXT,
    updated_at      TEXT NOT NULL,
    UNIQUE(code, event_id)
);

CREATE TABLE IF NOT EXISTS holder_num (
    code          TEXT NOT NULL,
    disclose_date TEXT NOT NULL,            -- 户数披露对应报告期或披露日
    holder_num    REAL NOT NULL,            -- 股东户数
    pct_change    REAL,                     -- 环比 %
    avg_hold      REAL,                     -- 户均持股
    total_mv      REAL,
    source        TEXT,
    PRIMARY KEY (code, disclose_date)
);

CREATE TABLE IF NOT EXISTS kline_daily (
    code       TEXT NOT NULL,
    trade_date TEXT NOT NULL,
    open       REAL, high REAL, low REAL, close REAL,
    volume     REAL, amount REAL, turnover REAL,
    adj_factor REAL,                       -- 复权因子（原始价 × 因子 = 后复权价）
    source     TEXT,
    PRIMARY KEY (code, trade_date)
);

-- 全市场基础刷新进度：按目标交易日和代码记录，可在三次触发点之间安全续跑。
CREATE TABLE IF NOT EXISTS market_refresh (
    run_date        TEXT PRIMARY KEY,
    status          TEXT NOT NULL,          -- partial/complete
    kline_total     INTEGER NOT NULL DEFAULT 0,
    kline_success   INTEGER NOT NULL DEFAULT 0,
    kline_skipped   INTEGER NOT NULL DEFAULT 0,
    kline_failed    INTEGER NOT NULL DEFAULT 0,
    longhu_status   TEXT NOT NULL DEFAULT 'pending', -- pending/green/known_empty/skipped
    blocking_reason TEXT,
    started_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    completed_at    TEXT,
    owner_token     TEXT,
    lease_until     TEXT
);

CREATE TABLE IF NOT EXISTS market_refresh_item (
    run_date    TEXT NOT NULL,
    code        TEXT NOT NULL,
    kind        TEXT NOT NULL,              -- kline
    status      TEXT NOT NULL,              -- pending/running/success/skipped/failed
    attempts    INTEGER NOT NULL DEFAULT 0,
    rows_count  INTEGER NOT NULL DEFAULT 0,
    last_error  TEXT,
    updated_at  TEXT NOT NULL,
    PRIMARY KEY (run_date, code, kind)
);
CREATE INDEX IF NOT EXISTS idx_market_refresh_item_status
    ON market_refresh_item(run_date, kind, status);

CREATE TABLE IF NOT EXISTS longhu (
    code       TEXT NOT NULL,
    trade_date TEXT NOT NULL,
    reason     TEXT NOT NULL,               -- 上榜原因
    net_buy    REAL,                        -- 净买额（万元）
    source     TEXT,
    UNIQUE(code, trade_date, reason)
);

-- 二期启用：偏离值/振幅/换手 × 龙虎榜比对 → "该上未上榜"
CREATE TABLE IF NOT EXISTS deviation_daily (
    code          TEXT NOT NULL,
    trade_date    TEXT NOT NULL,
    pct_chg       REAL,                     -- 个股涨跌幅 %
    index_pct_chg REAL,                     -- 对应板块指数涨跌幅 %
    deviation     REAL,                     -- 偏离值
    amplitude     REAL,                     -- 振幅 %
    turnover      REAL,                     -- 换手率 %
    should_flag   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (code, trade_date)
);

CREATE TABLE IF NOT EXISTS watch_note (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    code       TEXT NOT NULL,
    trade_date TEXT,
    content    TEXT NOT NULL,
    tags       TEXT,                        -- 逗号分隔
    created_at TEXT NOT NULL
);

-- 用户独立跟踪名单：移除只改为 history，不物理删除，便于恢复和复盘。
CREATE TABLE IF NOT EXISTS watchlist_entry (
    code       TEXT PRIMARY KEY,
    status     TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'history')),
    note       TEXT,
    added_at   TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    removed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_watchlist_status_updated
    ON watchlist_entry(status, updated_at DESC);

CREATE TABLE IF NOT EXISTS notification (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      TEXT NOT NULL,
    level   TEXT NOT NULL,                  -- info/warn/alert
    title   TEXT NOT NULL,
    body    TEXT,
    is_read INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS config (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,               -- JSON 值
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS scan_run (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_date    TEXT NOT NULL,              -- YYYY-MM-DD
    stage       TEXT NOT NULL,              -- fetch/announce/scan/refresh/...
    status      TEXT NOT NULL,              -- green/red
    stats_json  TEXT,
    errors      TEXT,
    started_at  TEXT NOT NULL,
    finished_at TEXT
);

-- M3 v6 冷启动导入（PRD §F7）：v6 筛选阶段被剔除标的的存档
CREATE TABLE IF NOT EXISTS rejected_record (
    code       TEXT,
    pub_date   TEXT,
    title      TEXT,
    reason     TEXT,
    created_at TEXT,
    UNIQUE(code, pub_date, title)
);

-- M4 流水线 B 因子扫描（PRD §3.2/§3.3/F5）：每日"疑似庄股榜"，同日重跑整日刷新
CREATE TABLE IF NOT EXISTS scan_result (
    scan_date       TEXT NOT NULL,           -- 扫描基准日 YYYY-MM-DD
    code            TEXT NOT NULL,
    gain_pct        REAL,                    -- 窗口起涨涨幅 %
    lhb_after_start INTEGER,                 -- 起涨点后龙虎榜次数（在榜行恒 0）
    holder_drop_pct REAL,                    -- 近 2 披露期最大单期户数降幅 %（负值）
    circ_mv         REAL,                    -- 流通市值（亿元）
    avg_amount      REAL,                    -- 窗口日均成交额（元）
    resonance       INTEGER NOT NULL,        -- 信号共振计数
    tier            TEXT,                    -- A / 观察
    hits_json       TEXT,                    -- 命中因子 JSON 数组
    start_date      TEXT,                    -- 起涨点（窗口最低价日）YYYY-MM-DD（v1.1）
    start_age_days  INTEGER,                 -- 起涨点距基准日交易日数（v1.1 及时性）
    lane            TEXT,                    -- 新起涨/延续观察/待确认
    mechanical_start_date TEXT,              -- 机械最低点
    confirmed_start_date  TEXT,              -- 确认起点
    missing_fields_json   TEXT,              -- 未补齐的数据字段
    PRIMARY KEY (scan_date, code)
);
CREATE INDEX IF NOT EXISTS idx_scan_result_date ON scan_result(scan_date);

CREATE TABLE IF NOT EXISTS holder_pct (
    code          TEXT NOT NULL,
    disclose_date TEXT NOT NULL,             -- 披露日 YYYY-MM-DD
    chain_pct     REAL,                      -- 户数环比变化 %
    baseline_pct  REAL,                      -- 相对事件基准累计 %（基准日=0）
    anchor_date   TEXT,                      -- 户数相对序列的基准日（资本运作日对应）
    source        TEXT NOT NULL,             -- v6
    imported_at   TEXT,
    PRIMARY KEY (code, disclose_date, source)
);

-- 用户人工评级（重要程度）：高=自动进“我的跟踪”；中/低=自动进“待观察”；
-- 有评级即视为“已看过”。(F12：评级与已看标记)
CREATE TABLE IF NOT EXISTS stock_rating (
    code        TEXT PRIMARY KEY,
    rating      TEXT NOT NULL CHECK(rating IN ('high', 'medium', 'low')),
    reviewed_at TEXT NOT NULL,               -- 首次评级时间（已看过标记）
    updated_at  TEXT NOT NULL,
    note        TEXT
);
