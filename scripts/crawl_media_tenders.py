"""Update media/publishing related active tenders from web.pcc.gov.tw.

The scraper uses the official Government e-Procurement search page, keeps only
content-production/editing/publishing cases, and writes normalized rows into the
existing SQLite database used by the FastAPI dashboard.
"""
import argparse
import json
import re
import time
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urljoin

import _bootstrap  # noqa: F401
from bs4 import BeautifulSoup

from app import db
from app.crawlers import pcc_g0v
from app.config import settings
from app.httpclient import make_client
from app.roc_date import roc_to_iso

BASE_URL = "https://web.pcc.gov.tw"
SEARCH_URL = f"{BASE_URL}/prkms/tender/common/basic/readTenderBasic"
SOURCE_NAME = "PCC official media watch"
SOURCE_STRATEGY = "official-html"

DEFAULT_TERMS = [
    "影片", "短影音", "影音", "影像", "紀錄片", "影展", "製播", "拍攝",
    "宣導", "宣傳", "行銷", "出版", "編輯", "編印", "印製", "手冊",
    "年報", "圖錄", "專刊", "專書", "文宣", "美編", "排版", "成果冊",
    "畫冊", "月刊", "書籍", "徵圖", "大賽", "紀錄", "節目", "媒體",
    "空拍", "空拍機", "航拍", "空中攝影", "無人機",
]

INCLUDE_RE = re.compile(
    r"影片(製作|製播|宣傳|宣導|行銷|拍攝|簡介|教材|典禮|形象|使用授權|大賽|紀錄|計畫)|"
    r"短影音|影展|紀錄影展|紀錄片|影像(紀錄|改編|特展|素材蒐集|拍攝)|"
    r"影音(簡介|平台|製作|設計|短片|教材)|製播|拍攝|動畫|節目|媒體行銷|"
    r"出版|編輯|編印|印刷委託|專刊|專書|圖錄|手冊|成果冊|文宣|月刊|畫冊|繪本|教材|攝影集|專輯|摺頁|導覽|排版|年報|徵圖|"
    r"空拍|空拍機|航拍|空中攝影|空中拍攝|空中影像"
)

EXCLUDE_RE = re.compile(
    r"醫療影像|影像醫學|影像部|影醫|監控|監視|攝影機|喉頭鏡|內視鏡|"
    r"顯微鏡|光碟|螢幕|顯示器|工作站|伺服器|儲存|冷氣|變流器|偵測|辨識|"
    r"醫材|衛材|手術|超音波|血流|插管|熱影像|檢測儀|會議系統|影音設備|"
    r"拍攝設備|攝影設備|攝錄設備|錄影設備|拍攝器材|攝影器材|水下拍攝設備|"
    r"視訊|數位化|維護|保養|購置|更新|汰換|安全輔助|治療儀|斷層|平台採購|"
    r"軟體|授權|license|License|Adobe|校園授權|教科書|書籍採購|圖書採購|圖書館書籍|"
    r"電子書採購|電子資源採購|書籍財物|書籍財務|書表|明信片|日曆|月曆|農民曆|春聯|紅包袋|表單|單據|"
    r"測驗本|測驗卷|評估量表|量表手冊|評量表|"
    r"直昇機|巡視|運載|礙子"
)

CONTENT_OVERRIDE_RE = re.compile(
    r"影片(製作|製播|拍攝|計畫|教材|使用授權)|短影音|紀錄片|影展|影音製作|"
    r"出版|編輯(採訪|設計|製作|企劃|排版|出版|委託|服務)|編印|印刷委託|成果冊|專書|專刊|圖錄|手冊|文宣|年報|排版|美編|摺頁|繪本|教材|攝影集|專輯|"
    r"空拍|航拍|空中攝影|空中拍攝"
)

HARD_EXCLUDE_RE = re.compile(
    r"Adobe|license|License|校園授權|軟體|軟體授權|教科書|書籍採購|圖書採購|"
    r"圖書館書籍|電子書採購|電子資源採購|書籍財物|書籍財務|書表|明信片|日曆|月曆|農民曆|春聯|紅包袋|"
    r"測驗本|測驗卷|評估量表|量表手冊|評量表|"
    r"影音設備|拍攝設備|攝影設備|攝錄設備|錄影設備|拍攝器材|攝影器材|水下拍攝設備|"
    r"直昇機|巡視|運載|礙子"
)

VIDEO_RE = re.compile(r"影片|短影音|影音|影展|紀錄片|影像紀錄|製播|拍攝|動畫|節目|媒體")
PUBLISH_RE = re.compile(r"出版|編輯|編印|印製|專刊|專書|圖錄|手冊|成果冊|文宣|月刊|畫冊|書籍|排版|年報")
AERIAL_RE = re.compile(r"空拍|空拍機|空中攝影|空中拍攝|空中影像|航拍|無人機|多旋翼|UAV")
PURE_PRINT_RE = re.compile(r"印製.*年報.*冊|年報.*印製.*冊")
PUBLISH_PRODUCTION_RE = re.compile(r"編輯|編印|美編|排版|設計|企劃|製作|委託|採訪|撰稿|攝影")

COMPARE_COLS = (
    "agency", "title", "type", "budget", "publish_date", "deadline",
    "deadline_time", "url", "tags",
)


def clean_text(value) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def parse_money(value: str) -> int | None:
    digits = re.sub(r"[^\d]", "", value or "")
    return int(digits) if digits else None


def parse_roc_date(value: str) -> str | None:
    iso, _ = roc_to_iso(clean_text(value))
    return iso


def split_date_time(value: str) -> tuple[str | None, str | None]:
    return roc_to_iso(clean_text(value))


def decode_script_title(raw: str) -> str:
    try:
        return json.loads(f'"{raw}"')
    except json.JSONDecodeError:
        return raw


def extract_title(row) -> str:
    for link in row.find_all("a"):
        title_attr = link.get("title") or ""
        if "標案名稱:" in title_attr:
            return clean_text(title_attr.split("標案名稱:", 1)[1])
    html = str(row)
    match = re.search(r'pageCode2Img\("((?:\\.|[^"\\])*)"\)', html)
    if match:
        return clean_text(decode_script_title(match.group(1)))
    return ""


def extract_href(row) -> str | None:
    for link in row.find_all("a", href=True):
        href = link["href"]
        if "urlSelector/common/tpam" in href:
            return urljoin(BASE_URL, href)
    return None


def extract_pk(url: str) -> str:
    match = re.search(r"[?&]pk=([^&]+)", url or "")
    return match.group(1) if match else ""


def parse_search_rows(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    table = soup.find("table", id="tpam")
    if not table:
        return []
    rows = table.select("tbody tr") or table.find_all("tr")[1:]
    parsed = []
    for row in rows:
        cells = row.find_all("td")
        if len(cells) < 10:
            continue
        if "無符合條件資料" in row.get_text(" ", strip=True):
            continue
        title = extract_title(row)
        official_url = extract_href(row)
        if not title or not official_url:
            continue
        case_cell_lines = [
            clean_text(x) for x in cells[2].get_text("\n", strip=True).splitlines()
            if clean_text(x)
        ]
        job_number = case_cell_lines[0] if case_cell_lines else ""
        publish_date = parse_roc_date(cells[6].get_text(" ", strip=True))
        deadline = parse_roc_date(cells[7].get_text(" ", strip=True))
        parsed.append({
            "agency": clean_text(cells[1].get_text(" ", strip=True)),
            "job_number": job_number,
            "title": title,
            "transmission_count": clean_text(cells[3].get_text(" ", strip=True)),
            "tender_way": clean_text(cells[4].get_text(" ", strip=True)),
            "nature": clean_text(cells[5].get_text(" ", strip=True)),
            "publish_date": publish_date,
            "deadline": deadline,
            "deadline_time": None,
            "budget": clean_text(cells[8].get_text(" ", strip=True)),
            "url": official_url,
            "pk": extract_pk(official_url),
        })
    return parsed


def label_value(lines: list[str], label: str) -> str | None:
    for i, line in enumerate(lines[:-1]):
        if line == label:
            return lines[i + 1]
    return None


def fetch_detail(client, url: str) -> dict:
    response = client.get(url)
    response.raise_for_status()
    html = response.text
    soup = BeautifulSoup(html, "lxml")
    lines = [clean_text(x) for x in soup.get_text("\n", strip=True).splitlines()]
    lines = [x for x in lines if x]
    plain_text = "\n".join(lines)
    if "驗證碼檢核" in plain_text or "撲克牌" in plain_text:
        raise RuntimeError("官方 detail 頁要求驗證碼，略過本次 detail 補值")

    detail = {"final_url": str(response.url)}
    match = re.search(r'var\s+checkSpdt\s*=\s*"([^"]+)"', html)
    if match:
        try:
            dt = datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S")
            detail["deadline"] = dt.strftime("%Y-%m-%d")
            detail["deadline_time"] = dt.strftime("%H:%M")
        except ValueError:
            pass

    if not detail.get("deadline"):
        deadline_raw = label_value(lines, "截止投標")
        deadline, deadline_time = split_date_time(deadline_raw or "")
        detail["deadline"] = deadline
        detail["deadline_time"] = deadline_time

    publish_raw = label_value(lines, "公告日")
    if publish_raw:
        detail["publish_date"] = parse_roc_date(publish_raw)

    for key in ("預算金額", "招標方式", "決標方式", "招標狀態", "履約期限", "聯絡人"):
        value = label_value(lines, key)
        if value:
            detail[key] = value
    return detail


def fetch_g0v_detail(client, row: dict) -> dict:
    """Fallback detail lookup through the g0v PCC API mirror.

    The official detail page can request a CAPTCHA after repeated calls. The
    list source remains web.pcc.gov.tw; this fallback only fills fields that
    are already public in PCC detail records.
    """
    title = row.get("title") or ""
    job_number = row.get("job_number") or ""
    if not title or not job_number:
        return {}
    search_terms = [title]
    if len(title) > 28:
        search_terms.append(title[:28])
    for term in search_terms:
        data = pcc_g0v.search_page(client, term, 1)
        for record in data.get("records", []) or []:
            if record.get("job_number") != job_number:
                continue
            if not record.get("unit_id"):
                continue
            detail_resp = pcc_g0v.fetch_detail(client, record["unit_id"], record["job_number"])
            fields = pcc_g0v._extract_detail_fields(detail_resp, record.get("filename"))
            return {
                "deadline": fields.get("deadline"),
                "deadline_time": fields.get("deadline_time"),
                "publish_date": fields.get("publish_date_detail"),
                "預算金額": fields.get("budget"),
                "fallback": "g0v PCC API",
            }
    return {}


def classify(row: dict) -> tuple[bool, list[str], list[str]]:
    text = f"{row.get('title', '')} {row.get('nature', '')} {row.get('tender_way', '')}"
    reasons = []
    include = bool(INCLUDE_RE.search(text))
    excluded = bool(EXCLUDE_RE.search(text))
    override = bool(CONTENT_OVERRIDE_RE.search(text))

    if not include:
        return False, [], ["未命中內容製作或出版編輯關鍵字"]
    if HARD_EXCLUDE_RE.search(text) and "影片使用授權" not in text:
        return False, [], ["硬體、軟體授權、教科書或圖書採購排除"]
    if PURE_PRINT_RE.search(text) and not PUBLISH_PRODUCTION_RE.search(text):
        return False, [], ["單純年報印製冊數案排除"]
    if excluded and not override:
        return False, [], ["疑似設備、醫療影像、監控、硬體、軟體授權或維護案"]

    tags = ["每日追蹤", "高相關"]
    if VIDEO_RE.search(text):
        tags.append("影片影音")
        reasons.append("影片/影音/影像內容相關")
    if PUBLISH_RE.search(text):
        tags.append("出版編輯")
        reasons.append("出版/編輯/編印/排版相關")
    if AERIAL_RE.search(text):
        tags.append("空拍")
        reasons.append("空拍/航拍/空中攝影/無人機相關")
    if "勞務" in row.get("nature", ""):
        tags.append("勞務")
    elif "財物" in row.get("nature", ""):
        tags.append("財物")

    return True, tags, reasons


def priority_tags(row: dict, today: date) -> list[str]:
    tags = []
    deadline = row.get("deadline")
    if deadline:
        try:
            days_left = (date.fromisoformat(deadline) - today).days
            if days_left <= 2:
                tags.append("急件")
            if days_left <= 7:
                tags.append("優先處理")
        except ValueError:
            pass
    amount = parse_money(row.get("budget") or "")
    if amount is not None and amount >= 1_000_000:
        tags.append("優先處理")
    return tags


def normalize_tags(tags: list[str]) -> list[str]:
    seen = set()
    result = []
    for tag in tags:
        if tag and tag not in seen:
            seen.add(tag)
            result.append(tag)
    return result


def existing_row(conn, row: dict) -> dict | None:
    found = conn.execute(
        "SELECT * FROM tenders WHERE unit_id=? AND job_number=? AND filename=?",
        (row.get("unit_id"), row.get("job_number"), row.get("filename")),
    ).fetchone()
    return dict(found) if found else None


def has_changed(existing: dict | None, row: dict) -> bool:
    if not existing:
        return True
    for col in COMPARE_COLS:
        if (existing.get(col) or "") != (row.get(col) or ""):
            return True
    return False


def search_term(client, term: str, pages: int) -> list[dict]:
    results = []
    for page in range(1, pages + 1):
        params = {
            "dateType": "isSpdt",
            "tenderType": "TENDER_DECLARATION",
            "tenderWay": "TENDER_WAY_ALL_DECLARATION",
            "searchType": "basic",
            "isBinding": "N",
            "pageSize": "100",
            "firstSearch": "true",
            "searchMethod": "true",
            "tenderName": term,
            "d-49738-p": str(page),
        }
        response = client.get(SEARCH_URL, params=params)
        response.raise_for_status()
        rows = parse_search_rows(response.text)
        if not rows:
            break
        for row in rows:
            row["matched_terms"] = [term]
            results.append(row)
        time.sleep(settings.CRAWL_DELAY)
    return results


def collect_candidates(client, terms: list[str], pages: int, progress=None) -> dict[str, dict]:
    candidates: dict[str, dict] = {}
    for term in terms:
        rows = search_term(client, term, pages)
        if progress:
            progress(f"[{term}] 搜尋 {len(rows)} 筆")
        for row in rows:
            key = row.get("pk") or f"{row.get('agency')}::{row.get('job_number')}::{row.get('title')}"
            if key in candidates:
                for term_name in row["matched_terms"]:
                    if term_name not in candidates[key]["matched_terms"]:
                        candidates[key]["matched_terms"].append(term_name)
                continue
            candidates[key] = row
    return candidates


def ensure_tag_rows(conn, tags: list[str]):
    for tag in tags:
        conn.execute("INSERT OR IGNORE INTO tags(name) VALUES(?)", (tag,))


def build_db_row(source_id: int, row: dict, tags: list[str], raw: dict) -> dict:
    return {
        "source_id": source_id,
        "unit_id": "web.pcc.gov.tw",
        "job_number": row.get("job_number") or row.get("pk") or row.get("title"),
        "filename": row.get("pk") or row.get("url"),
        "agency": row.get("agency"),
        "title": row.get("title"),
        "type": row.get("tender_way"),
        "budget": row.get("budget"),
        "publish_date": row.get("publish_date"),
        "deadline": row.get("deadline"),
        "deadline_time": row.get("deadline_time"),
        "url": row.get("url"),
        "tags": json.dumps(tags, ensure_ascii=False),
        "raw_json": json.dumps(raw, ensure_ascii=False),
    }


def prune_untracked_rows(conn, source_id: int, kept_filenames: set[str]) -> int:
    rows = conn.execute(
        "SELECT id, filename, tags FROM tenders WHERE source_id=?",
        (source_id,),
    ).fetchall()
    pruned = 0
    managed_tags = {
        "每日追蹤", "高相關", "影片影音", "出版編輯", "空拍", "優先處理", "急件", "勞務", "財物",
    }
    for row in rows:
        if row["filename"] in kept_filenames:
            continue
        try:
            tags = json.loads(row["tags"] or "[]")
        except json.JSONDecodeError:
            tags = []
        if "每日追蹤" not in tags:
            continue
        next_tags = [tag for tag in tags if tag not in managed_tags]
        if "排除" not in next_tags:
            next_tags.append("排除")
        conn.execute(
            "UPDATE tenders SET tags=?, updated_at=datetime('now','localtime') WHERE id=?",
            (json.dumps(next_tags, ensure_ascii=False), row["id"]),
        )
        pruned += 1
    return pruned


def ingest(args) -> dict:
    today = date.fromisoformat(args.today) if args.today else date.today()
    terms = [x.strip() for x in (args.terms or ",".join(DEFAULT_TERMS)).split(",") if x.strip()]
    stats = {
        "scanned": 0,
        "kept": 0,
        "excluded": 0,
        "inserted": 0,
        "changed": 0,
        "unchanged": 0,
        "detail_calls": 0,
        "detail_errors": 0,
        "g0v_detail_calls": 0,
        "g0v_detail_errors": 0,
        "errors": 0,
        "pruned": 0,
        "items": [],
    }

    def log(message: str):
        if not args.quiet:
            print(message)

    with (
        make_client(referer=BASE_URL + "/", accept="text/html,application/xhtml+xml") as client,
        pcc_g0v._client() as g0v_client,
    ):
        candidates = collect_candidates(client, terms, args.pages, progress=log)

        with db.get_conn() as conn:
            source_id = db.ensure_source(conn, SOURCE_NAME, BASE_URL, SOURCE_STRATEGY)
            run_id = db.start_crawl_run(conn, source_id, note=f"terms={len(terms)} pages={args.pages}")
            kept_filenames = set()
            detail_blocked = False
            try:
                for row in candidates.values():
                    stats["scanned"] += 1
                    keep, base_tags, reasons = classify(row)
                    if not keep:
                        stats["excluded"] += 1
                        continue
                    if row.get("deadline") and row["deadline"] < today.isoformat():
                        stats["excluded"] += 1
                        continue
                    if args.max_detail and stats["detail_calls"] < args.max_detail and not detail_blocked:
                        try:
                            detail = fetch_detail(client, row["url"])
                            stats["detail_calls"] += 1
                            row.update({k: v for k, v in detail.items()
                                        if k in ("deadline", "deadline_time", "publish_date") and v})
                            if detail.get("預算金額"):
                                row["budget"] = detail["預算金額"]
                            time.sleep(settings.CRAWL_DELAY)
                        except Exception as exc:  # noqa: BLE001
                            stats["detail_errors"] += 1
                            if "驗證碼" in str(exc):
                                detail_blocked = True
                            detail = {"error": str(exc)}
                    else:
                        detail = {}

                    if detail_blocked or (not row.get("deadline_time") and args.max_detail):
                        try:
                            fallback = fetch_g0v_detail(g0v_client, row)
                            if fallback:
                                stats["g0v_detail_calls"] += 1
                                row.update({k: v for k, v in fallback.items()
                                            if k in ("deadline", "deadline_time", "publish_date") and v})
                                if fallback.get("預算金額"):
                                    row["budget"] = fallback["預算金額"]
                                detail = {**detail, **fallback}
                                time.sleep(settings.CRAWL_DELAY)
                        except Exception as exc:  # noqa: BLE001
                            stats["g0v_detail_errors"] += 1
                            detail = {**detail, "g0v_error": str(exc)}

                    if not row.get("deadline") or row["deadline"] < today.isoformat():
                        stats["excluded"] += 1
                        continue

                    tags = normalize_tags(base_tags + priority_tags(row, today))
                    ensure_tag_rows(conn, tags)
                    kept_filenames.add(row.get("pk") or row.get("url") or "")
                    existing = existing_row(conn, {
                        "unit_id": "web.pcc.gov.tw",
                        "job_number": row.get("job_number") or row.get("pk") or row.get("title"),
                        "filename": row.get("pk") or row.get("url"),
                    })
                    if existing and not row.get("deadline_time") and existing.get("deadline_time"):
                        row["deadline_time"] = existing["deadline_time"]
                    if existing and not row.get("budget") and existing.get("budget"):
                        row["budget"] = existing["budget"]
                    raw = {
                        "official_row": row,
                        "detail": detail,
                        "matched_terms": row.get("matched_terms", []),
                        "reasons": reasons,
                    }
                    db_row = build_db_row(source_id, row, tags, raw)
                    changed = has_changed(existing, db_row)
                    if not args.dry_run:
                        action = db.upsert_tender(conn, db_row)
                    else:
                        action = "inserted" if existing is None else "updated"
                    if existing is None:
                        stats["inserted"] += 1
                    elif changed:
                        stats["changed"] += 1
                    else:
                        stats["unchanged"] += 1
                    stats["kept"] += 1
                    stats["items"].append({
                        "status": "new" if existing is None else ("changed" if changed else "unchanged"),
                        "action": action,
                        "deadline": row.get("deadline"),
                        "deadline_time": row.get("deadline_time"),
                        "budget": row.get("budget"),
                        "agency": row.get("agency"),
                        "title": row.get("title"),
                        "job_number": row.get("job_number"),
                        "tender_way": row.get("tender_way"),
                        "url": row.get("url"),
                        "tags": tags,
                    })
                if not args.no_prune and stats["errors"] == 0:
                    stats["pruned"] = prune_untracked_rows(conn, source_id, kept_filenames)
                db.mark_source_success(conn, source_id)
                db.finish_crawl_run(conn, run_id, "success", stats["kept"])
            except Exception as exc:  # noqa: BLE001
                db.finish_crawl_run(conn, run_id, "failed", stats["kept"], note=str(exc))
                raise

    stats["items"].sort(key=lambda x: (x.get("deadline") or "9999-99-99", x.get("deadline_time") or "99:99"))
    if args.report_json:
        path = Path(args.report_json)
        if not path.is_absolute():
            path = Path(__file__).resolve().parent.parent / path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    return stats


def main():
    parser = argparse.ArgumentParser(description="更新影片、影音、出版編輯相關等標期內標案")
    parser.add_argument("--pages", type=int, default=2, help="每個關鍵字抓取頁數")
    parser.add_argument("--max-detail", type=int, default=180, help="detail 頁抓取上限；0 表示不抓")
    parser.add_argument("--terms", default="", help="逗號分隔的搜尋關鍵字；預設用內建清單")
    parser.add_argument("--today", default="", help="覆寫今天日期 YYYY-MM-DD，測試用")
    parser.add_argument("--report-json", default="data/media_tenders_last_run.json", help="輸出本次更新摘要 JSON")
    parser.add_argument("--dry-run", action="store_true", help="只抓取與比對，不寫入 DB")
    parser.add_argument("--no-prune", action="store_true", help="不移除已不符合規則案件的每日追蹤標籤")
    parser.add_argument("--quiet", action="store_true", help="減少輸出")
    args = parser.parse_args()

    stats = ingest(args)
    print(
        "完成：掃描 {scanned}、保留 {kept}、新增 {inserted}、變更 {changed}、"
        "未變 {unchanged}、排除 {excluded}、移出追蹤 {pruned}、detail {detail_calls}、"
        "detail錯誤 {detail_errors}、g0v detail {g0v_detail_calls}、"
        "g0v錯誤 {g0v_detail_errors}、錯誤 {errors}".format(**stats)
    )
    priority_items = [
        item for item in stats["items"]
        if item["status"] in ("new", "changed") and "優先處理" in item.get("tags", [])
    ]
    for item in priority_items[:20]:
        print(
            f"- [{item['status']}] {item.get('deadline')} {item.get('deadline_time') or ''} "
            f"{item.get('agency')}｜{item.get('title')}｜{item.get('budget') or '未列預算'}｜{item.get('url')}"
        )


if __name__ == "__main__":
    main()
