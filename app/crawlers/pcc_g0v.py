"""標案爬蟲：g0v 政府電子採購網開放 API。

已於 2026-06 實測確認：
- API base 已搬到 https://pcc-api.openfun.app （舊網域 pcc.g0v.ronny.tw 會 301 但會丟失路徑）
- 必須帶瀏覽器 User-Agent + Referer，否則 403
- 搜尋：GET /api/searchbytitle?query=&page=
    回傳 {query, page, total_records, total_pages, took, records[]}
    record: {date(int YYYYMMDD), filename, brief:{type,title,companies}, job_number,
             unit_id, unit_name, tender_api_url, url}
- 詳情：GET /api/tender?unit_id=&job_number=
    回傳 {unit_name, records[]}；record.detail 是中文鍵物件，
    截止投標在含「截止投標」的鍵（民國年，例 "115/05/22 17:00"）。
"""
import json
import time
from typing import List, Optional

import httpx

from .. import db
from ..config import settings
from ..httpclient import make_client
from ..roc_date import roc_to_iso, yyyymmdd_to_iso


def _client() -> httpx.Client:
    return make_client(referer=settings.PCC_WEB_BASE + "/", accept="application/json")


def search_page(client: httpx.Client, query: str, page: int = 1) -> dict:
    r = client.get(f"{settings.PCC_API_BASE}/api/searchbytitle",
                   params={"query": query, "page": page})
    r.raise_for_status()
    return r.json()


def fetch_detail(client: httpx.Client, unit_id: str, job_number: str) -> dict:
    r = client.get(f"{settings.PCC_API_BASE}/api/tender",
                   params={"unit_id": unit_id, "job_number": job_number})
    r.raise_for_status()
    return r.json()


def _find_in_detail(detail: dict, needle: str) -> Optional[str]:
    """回傳第一個『鍵含 needle 且有值』的值。"""
    if not isinstance(detail, dict):
        return None
    for k, v in detail.items():
        if needle in k and v not in (None, "", "詳補充說明"):
            return str(v)
    return None


def _is_tender_type(type_str: str) -> bool:
    """招標類（有截止日）才值得抓 detail；決標/無法決標跳過。"""
    t = type_str or ""
    if "決標" in t:
        return False
    return ("招標" in t) or ("公開取得" in t) or ("資格" in t) or ("報價" in t)


def _extract_detail_fields(detail_resp: dict, filename: str) -> dict:
    """從 detail 回應挑出對應 filename 的紀錄，取截止日/公告日/預算。"""
    records = (detail_resp or {}).get("records", []) or []
    chosen = None
    for r in records:
        if r.get("filename") == filename:
            chosen = r
            break
    # 找不到對應 filename，就找第一筆含截止投標的
    if chosen is None:
        for r in records:
            if _find_in_detail(r.get("detail", {}), "截止投標"):
                chosen = r
                break
    if chosen is None and records:
        chosen = records[0]
    if chosen is None:
        return {}

    detail = chosen.get("detail", {}) or {}
    deadline_raw = _find_in_detail(detail, "截止投標")
    deadline_iso, deadline_time = roc_to_iso(deadline_raw)
    publish_raw = _find_in_detail(detail, "公告日")
    publish_iso, _ = roc_to_iso(publish_raw)
    budget = _find_in_detail(detail, "預算金額")
    return {
        "deadline": deadline_iso,
        "deadline_time": deadline_time,
        "publish_date_detail": publish_iso,
        "budget": budget,
        "raw": chosen,
    }


def ingest(
    queries: List[str],
    pages: int = 1,
    with_deadline: bool = True,
    max_detail: int = 120,
    progress=None,
    extra_tag: Optional[str] = None,
) -> dict:
    """抓取多個關鍵字、寫入 DB。

    with_deadline=True 時會對招標類記錄再打 detail 端點補截止日（較慢、較多請求）。
    max_detail 限制 detail 呼叫上限，避免一次打太多。
    """
    stats = {"inserted": 0, "updated": 0, "detail_calls": 0, "errors": 0, "scanned": 0}

    def log(msg):
        if progress:
            progress(msg)

    with _client() as client, db.get_conn() as conn:
        source_id = db.ensure_source(conn, "PCC g0v", settings.PCC_API_BASE, "api")
        run_id = db.start_crawl_run(conn, source_id, note=f"queries={queries} pages={pages}")
        try:
            for q in queries:
                for p in range(1, pages + 1):
                    try:
                        data = search_page(client, q, p)
                    except Exception as e:  # noqa: BLE001
                        stats["errors"] += 1
                        log(f"[{q}] page {p} 搜尋失敗：{e}")
                        break
                    records = data.get("records", []) or []
                    if not records:
                        break
                    log(f"[{q}] page {p}/{min(pages, data.get('total_pages', pages))} "
                        f"共 {data.get('total_records', '?')} 筆，本頁 {len(records)} 筆")
                    for rec in records:
                        stats["scanned"] += 1
                        brief = rec.get("brief", {}) or {}
                        type_str = brief.get("type", "")
                        row = {
                            "source_id": source_id,
                            "unit_id": rec.get("unit_id"),
                            "job_number": rec.get("job_number"),
                            "filename": rec.get("filename"),
                            "agency": rec.get("unit_name"),
                            "title": brief.get("title"),
                            "type": type_str,
                            "budget": None,
                            "publish_date": yyyymmdd_to_iso(rec.get("date")),
                            "deadline": None,
                            "deadline_time": None,
                            "url": settings.PCC_WEB_BASE + (rec.get("url") or ""),
                            "tags": json.dumps([extra_tag], ensure_ascii=False) if extra_tag else None,
                            "raw_json": json.dumps(rec, ensure_ascii=False),
                        }
                        if (with_deadline and stats["detail_calls"] < max_detail
                                and _is_tender_type(type_str)
                                and rec.get("unit_id") and rec.get("job_number")):
                            try:
                                dd = fetch_detail(client, rec["unit_id"], rec["job_number"])
                                stats["detail_calls"] += 1
                                fields = _extract_detail_fields(dd, rec.get("filename"))
                                row["deadline"] = fields.get("deadline")
                                row["deadline_time"] = fields.get("deadline_time")
                                row["budget"] = fields.get("budget")
                                if fields.get("publish_date_detail"):
                                    row["publish_date"] = fields["publish_date_detail"]
                                if fields.get("raw"):
                                    row["raw_json"] = json.dumps(fields["raw"], ensure_ascii=False)
                                time.sleep(settings.CRAWL_DELAY)
                            except Exception as e:  # noqa: BLE001
                                stats["errors"] += 1
                                log(f"  detail 失敗 {rec.get('job_number')}：{e}")
                        action = db.upsert_tender(conn, row)
                        stats[action] += 1
                    conn.commit()
                    time.sleep(settings.CRAWL_DELAY)
            db.mark_source_success(conn, source_id)
            db.finish_crawl_run(conn, run_id, "success",
                                stats["inserted"] + stats["updated"])
        except Exception as e:  # noqa: BLE001
            db.finish_crawl_run(conn, run_id, "failed",
                                stats["inserted"] + stats["updated"], note=str(e))
            raise
    return stats
