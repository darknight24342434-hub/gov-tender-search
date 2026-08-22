"""g0v PCC 決標行情 crawler."""
import json
import re
import time
from typing import Iterable, Optional

import httpx

from .. import db
from ..config import settings
from ..httpclient import make_client
from ..roc_date import yyyymmdd_to_iso


DETAIL_429_RETRIES = 3
DETAIL_429_BASE_DELAY = 2.0
BUDGET_KEY = "採購資料:預算金額"
BUDGET_KEY_SUFFIX = ":預算金額"
BUDGET_NEEDLE = "預算金額"


def _client() -> httpx.Client:
    return make_client(referer=settings.PCC_WEB_BASE + "/", accept="application/json")


def search_page(client: httpx.Client, query: str, page: int = 1) -> dict:
    r = client.get(
        f"{settings.PCC_API_BASE}/api/searchbytitle",
        params={"query": query, "page": page},
    )
    r.raise_for_status()
    return r.json()


def fetch_detail(client: httpx.Client, unit_id: str, job_number: str) -> dict:
    for attempt in range(DETAIL_429_RETRIES + 1):
        r = client.get(
            f"{settings.PCC_API_BASE}/api/tender",
            params={"unit_id": unit_id, "job_number": job_number},
        )
        if r.status_code == 429 and attempt < DETAIL_429_RETRIES:
            time.sleep(DETAIL_429_BASE_DELAY * (2**attempt))
            continue
        r.raise_for_status()
        return r.json()
    raise RuntimeError("unreachable detail retry state")


def _detail_delay() -> float:
    return max(settings.CRAWL_DELAY, 1.0)


def _is_award_record(record: dict) -> bool:
    brief = record.get("brief", {}) or {}
    return "決標" in str(brief.get("type") or "")


def _money_to_int(value) -> Optional[int]:
    if value in (None, ""):
        return None
    text = str(value)
    digits = re.sub(r"[^\d]", "", text)
    return int(digits) if digits else None


def _int_from_text(value) -> Optional[int]:
    if value in (None, ""):
        return None
    match = re.search(r"\d+", str(value))
    return int(match.group(0)) if match else None


def _winner_from_brief(brief: dict) -> Optional[str]:
    companies = (brief or {}).get("companies") or {}
    names = companies.get("names") if isinstance(companies, dict) else None
    if isinstance(names, list):
        return "、".join(str(n).strip() for n in names if str(n).strip()) or None
    if names:
        return str(names).strip() or None
    return None


def _iter_dicts(value) -> Iterable[dict]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _iter_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _iter_dicts(child)


def _find_value_by_key(detail: dict, needle: str) -> Optional[str]:
    for item in _iter_dicts(detail):
        for key, value in item.items():
            if needle in str(key) and value not in (None, ""):
                return str(value)
    return None


def _find_budget(detail: dict) -> Optional[int]:
    for item in _iter_dicts(detail):
        for key, value in item.items():
            key_text = str(key)
            if key_text == BUDGET_KEY or key_text.endswith(BUDGET_KEY_SUFFIX):
                amount = _money_to_int(value)
                if amount is not None:
                    return amount
    for item in _iter_dicts(detail):
        for key, value in item.items():
            if BUDGET_NEEDLE in str(key):
                amount = _money_to_int(value)
                if amount is not None:
                    return amount
    return None

def _find_award_amount(detail: dict) -> Optional[int]:
    for item in _iter_dicts(detail):
        for key, value in item.items():
            key_text = str(key)
            if "決標金額" in key_text and value not in (None, ""):
                return _money_to_int(value)
    return None


def _find_record_detail(detail_resp: dict, filename: Optional[str]) -> dict:
    records = (detail_resp or {}).get("records", []) or []
    chosen = None
    for record in records:
        if record.get("filename") == filename:
            chosen = record
            break
    if chosen is None and records:
        chosen = records[0]
    return chosen or {}


def _absolute_url(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    if str(url).startswith(("http://", "https://")):
        return str(url)
    return settings.PCC_WEB_BASE + str(url)


def _ratio(award_amount: Optional[int], budget: Optional[int]) -> Optional[float]:
    if not award_amount or not budget:
        return None
    return round(award_amount / budget, 3)


def _row_from_record(source_record: dict, detail_record: Optional[dict]) -> dict:
    brief = source_record.get("brief", {}) or {}
    detail = (detail_record or {}).get("detail", {}) or {}
    winner = _winner_from_brief(brief) or _find_value_by_key(
        detail, "決標品項:第1品項:得標廠商1:得標廠商"
    )
    budget = _find_budget(detail)
    award_amount = _find_award_amount(detail)
    bidders = _int_from_text(_find_value_by_key(detail, "投標廠商家數"))
    raw = {"search": source_record}
    if detail_record:
        raw["detail"] = detail_record
    return {
        "unit_id": source_record.get("unit_id"),
        "job_number": source_record.get("job_number"),
        "filename": source_record.get("filename"),
        "agency": source_record.get("unit_name"),
        "title": brief.get("title"),
        "winner": winner,
        "budget": budget,
        "award_amount": award_amount,
        "ratio": _ratio(award_amount, budget),
        "bidders": bidders,
        "award_date": yyyymmdd_to_iso(source_record.get("date")),
        "url": _absolute_url(source_record.get("url")),
        "raw_json": json.dumps(raw, ensure_ascii=False),
    }


def crawl_awards(queries, pages: int = 1, max_detail: int = 200, progress=None) -> dict:
    """搜尋決標公告、抽取行情欄位並寫入 awards。"""
    db.init_db()
    stats = {
        "inserted": 0,
        "updated": 0,
        "detail_calls": 0,
        "detail_errors": 0,
        "errors": 0,
        "scanned": 0,
        "awards": 0,
    }

    def log(message: str):
        if progress:
            progress(message)

    with _client() as client, db.get_conn() as conn:
        source_id = db.ensure_source(conn, "PCC g0v awards", settings.PCC_API_BASE, "api")
        run_id = db.start_crawl_run(conn, source_id, note=f"queries={queries} pages={pages}")
        try:
            for query in queries:
                for page in range(1, pages + 1):
                    try:
                        data = search_page(client, query, page)
                    except Exception as exc:  # noqa: BLE001
                        stats["errors"] += 1
                        log(f"[{query}] page {page} 搜尋失敗：{exc}")
                        break
                    records = data.get("records", []) or []
                    if not records:
                        break
                    log(f"[{query}] page {page} records={len(records)}")
                    for record in records:
                        stats["scanned"] += 1
                        if not _is_award_record(record):
                            continue
                        stats["awards"] += 1
                        detail_record = None
                        if (
                            stats["detail_calls"] < max_detail
                            and record.get("unit_id")
                            and record.get("job_number")
                        ):
                            try:
                                detail_resp = fetch_detail(
                                    client, record["unit_id"], record["job_number"]
                                )
                                stats["detail_calls"] += 1
                                detail_record = _find_record_detail(
                                    detail_resp, record.get("filename")
                                )
                                time.sleep(_detail_delay())
                            except Exception as exc:  # noqa: BLE001
                                stats["detail_errors"] += 1
                                log(f"  detail 跳過 {record.get('job_number')}：{exc}")
                        action = db.upsert_award(conn, _row_from_record(record, detail_record))
                        stats[action] += 1
                    conn.commit()
                    time.sleep(settings.CRAWL_DELAY)
            db.mark_source_success(conn, source_id)
            db.finish_crawl_run(conn, run_id, "success", stats["inserted"] + stats["updated"])
        except Exception as exc:  # noqa: BLE001
            db.finish_crawl_run(
                conn, run_id, "failed", stats["inserted"] + stats["updated"], note=str(exc)
            )
            raise
    return stats
