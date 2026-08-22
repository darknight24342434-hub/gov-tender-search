"""SQLite 存取層：連線、建表、種子資料、upsert 與爬蟲紀錄。"""
import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable, Optional

from .config import settings

SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"

# 預設標籤詞彙（智慧標籤分類的固定選項，降低 AI 輸出變異）
DEFAULT_TAGS = [
    "資訊軟體", "資安", "工程營造", "醫療衛生", "教育學習",
    "環境保護", "交通運輸", "文化觀光", "社會福利", "農業",
    "國防", "顧問規劃", "研究計畫", "設備採購", "行銷宣傳",
    "活動辦理", "清潔維護", "能源", "法律財會", "其他",
]


@contextmanager
def get_conn():
    os.makedirs(os.path.dirname(settings.DB_PATH), exist_ok=True)
    conn = sqlite3.connect(settings.DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    """建立資料表並寫入種子標籤。"""
    sql = SCHEMA_PATH.read_text(encoding="utf-8")
    with get_conn() as conn:
        conn.executescript(sql)
        tender_cols = {
            row["name"] for row in conn.execute("PRAGMA table_info(tenders)").fetchall()
        }
        if "dissect" not in tender_cols:
            conn.execute("ALTER TABLE tenders ADD COLUMN dissect TEXT")
        for t in DEFAULT_TAGS:
            conn.execute("INSERT OR IGNORE INTO tags(name) VALUES(?)", (t,))


def list_tags(conn) -> list:
    rows = conn.execute("SELECT name FROM tags ORDER BY id").fetchall()
    return [r["name"] for r in rows]


def ensure_source(conn, name: str, base_url: str, strategy: str) -> int:
    conn.execute(
        "INSERT OR IGNORE INTO sources(name, base_url, strategy) VALUES(?,?,?)",
        (name, base_url, strategy),
    )
    row = conn.execute("SELECT id FROM sources WHERE name=?", (name,)).fetchone()
    return row["id"]


def mark_source_success(conn, source_id: int):
    conn.execute(
        "UPDATE sources SET last_success_at=datetime('now','localtime') WHERE id=?",
        (source_id,),
    )


def start_crawl_run(conn, source_id: int, note: str = "") -> int:
    cur = conn.execute(
        "INSERT INTO crawl_runs(source_id, status, note) VALUES(?, 'running', ?)",
        (source_id, note),
    )
    return cur.lastrowid


def finish_crawl_run(conn, run_id: int, status: str, fetched: int, note: Optional[str] = None):
    conn.execute(
        "UPDATE crawl_runs SET finished_at=datetime('now','localtime'), status=?, "
        "fetched_count=?, note=COALESCE(?, note) WHERE id=?",
        (status, fetched, note, run_id),
    )


# ---------- upsert ----------

def _exists(conn, table: str, where: str, params: Iterable) -> Optional[int]:
    row = conn.execute(f"SELECT id FROM {table} WHERE {where}", tuple(params)).fetchone()
    return row["id"] if row else None


def upsert_tender(conn, row: dict) -> str:
    """依 (unit_id, job_number, filename) upsert。回傳 'inserted' 或 'updated'。"""
    existing = _exists(
        conn, "tenders",
        "unit_id=? AND job_number=? AND filename=?",
        (row.get("unit_id"), row.get("job_number"), row.get("filename")),
    )
    cols = ["source_id", "unit_id", "job_number", "filename", "agency", "title",
            "type", "budget", "publish_date", "deadline", "deadline_time", "url",
            "tags", "raw_json"]
    vals = [row.get(c) for c in cols]
    if existing:
        sets = ", ".join(f"{c}=?" for c in cols)
        conn.execute(
            f"UPDATE tenders SET {sets}, updated_at=datetime('now','localtime') WHERE id=?",
            (*vals, existing),
        )
        return "updated"
    placeholders = ", ".join("?" for _ in cols)
    conn.execute(
        f"INSERT INTO tenders({', '.join(cols)}) VALUES({placeholders})", vals
    )
    return "inserted"


def upsert_grant(conn, row: dict) -> str:
    """依 url upsert。回傳 'inserted' 或 'updated'。"""
    existing = _exists(conn, "grants", "url=?", (row.get("url"),))
    cols = ["source_id", "agency", "title", "target", "tags",
            "apply_start", "apply_end", "url", "raw_json"]
    vals = [row.get(c) for c in cols]
    if existing:
        sets = ", ".join(f"{c}=?" for c in cols)
        conn.execute(
            f"UPDATE grants SET {sets}, updated_at=datetime('now','localtime') WHERE id=?",
            (*vals, existing),
        )
        return "updated"
    placeholders = ", ".join("?" for _ in cols)
    conn.execute(f"INSERT INTO grants({', '.join(cols)}) VALUES({placeholders})", vals)
    return "inserted"


def upsert_award(conn, row: dict) -> str:
    """依 (unit_id, job_number, filename) upsert 決標行情。"""
    existing = _exists(
        conn,
        "awards",
        "unit_id=? AND job_number=? AND filename=?",
        (row.get("unit_id"), row.get("job_number"), row.get("filename")),
    )
    cols = [
        "unit_id", "job_number", "filename", "agency", "title", "winner",
        "budget", "award_amount", "ratio", "bidders", "award_date", "url",
        "raw_json",
    ]
    vals = [row.get(c) for c in cols]
    if existing:
        sets = ", ".join(f"{c}=?" for c in cols)
        conn.execute(f"UPDATE awards SET {sets} WHERE id=?", (*vals, existing))
        return "updated"
    placeholders = ", ".join("?" for _ in cols)
    conn.execute(f"INSERT INTO awards({', '.join(cols)}) VALUES({placeholders})", vals)
    return "inserted"


# 這些函式會把 table 名稱拼進 SQL（SQLite 不接受把識別字當參數綁定），
# 所以只准白名單內的表名，而且在碰到資料庫之前就擋。
ENRICHABLE_TABLES = ("tenders", "grants", "awards")


def require_known_table(table: str) -> str:
    """回傳合法的表名，否則丟 ValueError。"""
    if table not in ENRICHABLE_TABLES:
        raise ValueError(
            f"unsupported table {table!r}; allowed tables are {', '.join(ENRICHABLE_TABLES)}"
        )
    return table


def set_tags(conn, table: str, row_id: int, tags: list):
    require_known_table(table)
    conn.execute(
        f"UPDATE {table} SET tags=?, updated_at=datetime('now','localtime') WHERE id=?",
        (json.dumps(tags, ensure_ascii=False), row_id),
    )


def set_summary(conn, table: str, row_id: int, summary: dict):
    require_known_table(table)
    conn.execute(
        f"UPDATE {table} SET summary=?, updated_at=datetime('now','localtime') WHERE id=?",
        (json.dumps(summary, ensure_ascii=False), row_id),
    )


def set_dissect(conn, row_id: int, dissect: dict):
    conn.execute(
        "UPDATE tenders SET dissect=?, updated_at=datetime('now','localtime') WHERE id=?",
        (json.dumps(dissect, ensure_ascii=False), row_id),
    )
