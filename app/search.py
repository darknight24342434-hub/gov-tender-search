"""搜尋查詢建構。中文用 LIKE 子字串比對（比 FTS5 預設分詞對 CJK 更可靠）。"""
import json
from typing import List, Optional

from . import db

PAGE_SIZE = 20


def _row_to_dict(row) -> dict:
    d = dict(row)
    for field in ("tags", "summary"):
        if d.get(field):
            try:
                d[field] = json.loads(d[field])
            except (ValueError, TypeError):
                pass
    d.pop("raw_json", None)  # 列表不回傳原始 JSON，省頻寬
    return d


def search_tenders(
    conn,
    q: Optional[str] = None,
    tags: Optional[List[str]] = None,
    deadline_from: Optional[str] = None,
    deadline_to: Optional[str] = None,
    type_: Optional[str] = None,
    only_active: bool = False,
    page: int = 1,
    page_size: int = PAGE_SIZE,
) -> dict:
    where, params = [], []
    if q:
        where.append("(title LIKE ? OR agency LIKE ? OR job_number LIKE ?)")
        like = f"%{q}%"
        params += [like, like, like]
    for t in tags or []:
        where.append("tags LIKE ?")
        params.append(f'%"{t}"%')
    if deadline_from:
        where.append("deadline IS NOT NULL AND deadline >= ?")
        params.append(deadline_from)
    if deadline_to:
        where.append("deadline IS NOT NULL AND deadline <= ?")
        params.append(deadline_to)
    if type_:
        where.append("type LIKE ?")
        params.append(f"%{type_}%")
    if only_active:
        where.append("deadline IS NOT NULL AND deadline >= date('now','localtime')")

    clause = (" WHERE " + " AND ".join(where)) if where else ""
    total = conn.execute(f"SELECT COUNT(*) AS n FROM tenders{clause}", params).fetchone()["n"]

    # 有截止日的排前面、近的排前面；其餘依公告日新到舊
    order = " ORDER BY (deadline IS NULL), deadline ASC, publish_date DESC, id DESC"
    offset = max(0, (page - 1)) * page_size
    rows = conn.execute(
        f"SELECT * FROM tenders{clause}{order} LIMIT ? OFFSET ?",
        (*params, page_size, offset),
    ).fetchall()
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [_row_to_dict(r) for r in rows],
    }


def search_grants(
    conn,
    q: Optional[str] = None,
    tags: Optional[List[str]] = None,
    target: Optional[str] = None,
    deadline_from: Optional[str] = None,
    only_active: bool = False,
    page: int = 1,
    page_size: int = PAGE_SIZE,
) -> dict:
    where, params = [], []
    if q:
        where.append("(title LIKE ? OR agency LIKE ?)")
        like = f"%{q}%"
        params += [like, like]
    for t in tags or []:
        where.append("tags LIKE ?")
        params.append(f'%"{t}"%')
    if target:
        where.append("target LIKE ?")
        params.append(f"%{target}%")
    if deadline_from:
        where.append("apply_end IS NOT NULL AND apply_end >= ?")
        params.append(deadline_from)
    if only_active:
        where.append("(apply_end IS NULL OR apply_end >= date('now','localtime'))")

    clause = (" WHERE " + " AND ".join(where)) if where else ""
    total = conn.execute(f"SELECT COUNT(*) AS n FROM grants{clause}", params).fetchone()["n"]
    order = """ ORDER BY
  CASE WHEN apply_end IS NULL THEN 1
       WHEN apply_end >= date('now','localtime') THEN 0
       ELSE 2 END,
  CASE WHEN apply_end IS NOT NULL AND apply_end >= date('now','localtime') THEN apply_end END ASC,
  CASE WHEN apply_end IS NOT NULL AND apply_end <  date('now','localtime') THEN apply_end END DESC,
  id DESC"""
    offset = max(0, (page - 1)) * page_size
    rows = conn.execute(
        f"SELECT * FROM grants{clause}{order} LIMIT ? OFFSET ?",
        (*params, page_size, offset),
    ).fetchall()
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [_row_to_dict(r) for r in rows],
    }


def get_one(conn, table: str, row_id: int) -> Optional[dict]:
    db.require_known_table(table)
    row = conn.execute(f"SELECT * FROM {table} WHERE id=?", (row_id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    for field in ("tags", "summary", "raw_json"):
        if d.get(field):
            try:
                d[field] = json.loads(d[field])
            except (ValueError, TypeError):
                pass
    return d
