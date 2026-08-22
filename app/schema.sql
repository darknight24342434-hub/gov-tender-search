-- 政府標案 / 補助案搜尋系統 資料表
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- 來源台帳
CREATE TABLE IF NOT EXISTS sources (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL UNIQUE,
    base_url        TEXT,
    strategy        TEXT,            -- api / html / manual
    last_success_at TEXT
);

-- 受控標籤詞彙（智慧標籤分類用）
CREATE TABLE IF NOT EXISTS tags (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL UNIQUE,
    parent_category TEXT
);

-- 標案
CREATE TABLE IF NOT EXISTS tenders (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id     INTEGER REFERENCES sources(id),
    unit_id       TEXT,
    job_number    TEXT,
    filename      TEXT,
    agency        TEXT,              -- 機關名稱 unit_name
    title         TEXT,
    type          TEXT,              -- 招標公告 / 決標公告 / ...
    budget        TEXT,              -- 預算金額（原字串，可能含「元」或保密）
    publish_date  TEXT,              -- 公告日 ISO
    deadline      TEXT,              -- 截止投標 ISO（可為 NULL）
    deadline_time TEXT,              -- 截止時間 HH:MM
    url           TEXT,              -- 可瀏覽的原始連結
    tags          TEXT,              -- JSON 陣列字串，例 ["資安","軟體開發"]
    summary       TEXT,              -- JSON：{who, threshold, deadline, next_step}
    dissect       TEXT,              -- JSON：AI 標書解構卡
    raw_json      TEXT,              -- 原始資料備查
    created_at    TEXT DEFAULT (datetime('now','localtime')),
    updated_at    TEXT DEFAULT (datetime('now','localtime')),
    UNIQUE(unit_id, job_number, filename)
);
CREATE INDEX IF NOT EXISTS idx_tenders_deadline ON tenders(deadline);
CREATE INDEX IF NOT EXISTS idx_tenders_publish ON tenders(publish_date);
CREATE INDEX IF NOT EXISTS idx_tenders_type ON tenders(type);

-- 決標行情
CREATE TABLE IF NOT EXISTS awards (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    unit_id       TEXT,
    job_number    TEXT,
    filename      TEXT,
    agency        TEXT,
    title         TEXT,
    winner        TEXT,
    budget        INTEGER,
    award_amount  INTEGER,
    ratio         REAL,
    bidders       INTEGER,
    award_date    TEXT,
    url           TEXT,
    raw_json      TEXT,
    created_at    TEXT DEFAULT (datetime('now','localtime')),
    UNIQUE(unit_id, job_number, filename)
);
CREATE INDEX IF NOT EXISTS idx_awards_date ON awards(award_date);
CREATE INDEX IF NOT EXISTS idx_awards_agency ON awards(agency);
CREATE INDEX IF NOT EXISTS idx_awards_winner ON awards(winner);

-- 補助案
CREATE TABLE IF NOT EXISTS grants (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id    INTEGER REFERENCES sources(id),
    agency       TEXT,               -- 主辦機關
    title        TEXT,
    target       TEXT,               -- 適用對象
    tags         TEXT,               -- JSON 陣列字串
    apply_start  TEXT,               -- 申請起 ISO
    apply_end    TEXT,               -- 申請迄 ISO（= 截止日，可為 NULL）
    url          TEXT,
    summary      TEXT,               -- JSON：{who, threshold, deadline, next_step}
    raw_json     TEXT,
    created_at   TEXT DEFAULT (datetime('now','localtime')),
    updated_at   TEXT DEFAULT (datetime('now','localtime')),
    UNIQUE(url)
);
CREATE INDEX IF NOT EXISTS idx_grants_end ON grants(apply_end);

-- 爬蟲執行紀錄
CREATE TABLE IF NOT EXISTS crawl_runs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id     INTEGER REFERENCES sources(id),
    started_at    TEXT DEFAULT (datetime('now','localtime')),
    finished_at   TEXT,
    status        TEXT,              -- running / success / failed
    fetched_count INTEGER DEFAULT 0,
    note          TEXT
);
