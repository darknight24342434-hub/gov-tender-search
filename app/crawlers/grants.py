"""補助案來源。

現況（誠實說明，2026-06）：台灣政府補助沒有單一乾淨 API，各部會散落、版型各異。

已攻克並實測的爬蟲（HTML_CRAWLERS，crawl_all 會全跑）：
  - startup_sme：新創圓夢網「政府資源總覽」（中小企業署彙整各部會資源：
    SBIR/SIIR/創業貸款/天使投資/各類補助…，分頁，~267 筆）。
  - moc：文化部獎補助資訊網（反解其 JSON API，不需 viewstate；含申請起訖截止日）。

另有 Playwright 爬蟲（見 pw_moea.py，需真實 Chrome，不放進 crawl_all）：
  - moea：經濟部補助計畫入口網（Cloudflare 後面，headed 真 Chrome 過 CF，~30 筆）。

已查證無資料：
  - 中小企業署 www.sme.gov.tw/list-tw-2411：實測「補助類」分類「共 0 筆」，
    連 AJAX 請求都沒發（頁面本身就空），非爬取問題；其補助內容走 startup_sme。

另提供：
  (B) ingest_seed()——從 JSON 匯入人工整理的清單，補上爬蟲涵蓋不到的部會。
  (C) HtmlSource 通用骨架——填好 selector 即可接新站。
"""
import base64
import html
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional
from urllib.parse import unquote, urljoin

from .. import db
from ..config import settings
from ..httpclient import make_client
from ..roc_date import normalize_iso, roc_to_iso


# ============================================================
# (A) 已實作爬蟲：新創圓夢網「政府資源總覽」
# ============================================================
STARTUP_SME_LIST = "https://startup.sme.gov.tw/home/modules/rproject/index.php"
AI_GRANT_KEYWORDS = (
    "AI", "Ａ.Ｉ", "人工智慧", "人工智能", "智慧", "智能", "機器學習", "深度學習",
    "生成式", "大數據", "巨量資料", "演算法", "資料科學", "LLM", "大型語言模型",
    "RPA", "機器人流程", "電腦視覺", "影像辨識", "自然語言", "智慧製造",
    "智慧城市", "AIoT", "數位轉型", "數位創新",
)
VIDEO_GRANT_KEYWORDS = (
    "影片", "影音", "短影音", "影像", "紀錄片", "微電影", "拍攝", "攝影",
    "製播", "動畫", "多媒體", "影展", "宣傳片", "廣告片", "影視", "MV",
    "數位內容", "串流", "OTT",
)
CULTURE_GRANT_KEYWORDS = (
    "文化", "文創", "藝文", "藝術", "表演藝術", "展覽", "策展", "出版",
    "文學", "音樂", "工藝", "文資", "文化資產", "古蹟", "遺產", "社區營造",
    "地方創生", "傳統", "節慶", "博物館", "圖書", "獎補助",
)
EDUCATION_GRANT_KEYWORDS = (
    "教育", "研習", "教師", "師資", "師培", "國中小", "國小", "國中", "中小學",
    "校園", "數位學習", "數位教學", "增能", "人才培育", "素養", "示範學校",
    "智慧教育", "適性學習", "教學現場", "生生用平板", "學校", "教材",
)
_AI_GRANT_ASCII_PATTERNS = tuple(
    re.compile(rf"(?<![A-Za-z0-9]){re.escape(keyword)}(?![A-Za-z0-9])", re.IGNORECASE)
    for keyword in ("AI", "LLM", "RPA", "AIoT")
)
_AI_GRANT_TEXT_KEYWORDS = tuple(
    keyword for keyword in AI_GRANT_KEYWORDS
    if keyword not in {"AI", "LLM", "RPA", "AIoT"}
)
_VIDEO_GRANT_ASCII_PATTERNS = tuple(
    re.compile(rf"(?<![A-Za-z0-9]){re.escape(keyword)}(?![A-Za-z0-9])", re.IGNORECASE)
    for keyword in ("MV", "OTT")
)
_VIDEO_GRANT_TEXT_KEYWORDS = tuple(
    keyword for keyword in VIDEO_GRANT_KEYWORDS
    if keyword not in {"MV", "OTT"}
)
_CULTURE_GRANT_TEXT_KEYWORDS = CULTURE_GRANT_KEYWORDS
_EDUCATION_GRANT_TEXT_KEYWORDS = EDUCATION_GRANT_KEYWORDS


def is_ai_grant_text(title: str = "", summary: str = "", description: str = "") -> bool:
    """只比對標題、摘要與 raw_json.description，不比對 URL 或整段 raw_json。"""
    haystack = "\n".join([title or "", summary or "", description or ""])
    return (
        any(pattern.search(haystack) for pattern in _AI_GRANT_ASCII_PATTERNS)
        or any(keyword in haystack for keyword in _AI_GRANT_TEXT_KEYWORDS)
    )


def is_video_grant_text(title: str = "", summary: str = "", description: str = "") -> bool:
    """只比對標題、摘要與 raw_json.description，不比對 URL 或整段 raw_json。"""
    haystack = "\n".join([title or "", summary or "", description or ""])
    return (
        any(pattern.search(haystack) for pattern in _VIDEO_GRANT_ASCII_PATTERNS)
        or any(keyword in haystack for keyword in _VIDEO_GRANT_TEXT_KEYWORDS)
    )


def is_culture_grant_text(title: str = "", summary: str = "", description: str = "") -> bool:
    """只比對標題、摘要與 raw_json.description，不比對 URL 或整段 raw_json。"""
    haystack = "\n".join([title or "", summary or "", description or ""])
    return any(keyword in haystack for keyword in _CULTURE_GRANT_TEXT_KEYWORDS)


def is_education_grant_text(title: str = "", summary: str = "", description: str = "") -> bool:
    """只比對標題、摘要與 raw_json.description，不比對 URL 或整段 raw_json。"""
    haystack = "\n".join([title or "", summary or "", description or ""])
    return any(keyword in haystack for keyword in _EDUCATION_GRANT_TEXT_KEYWORDS)


def append_ai_tag_if_matched(tags: list, title: str = "", summary: str = "", description: str = "") -> list:
    out = list(dict.fromkeys(t for t in tags if t))
    if is_ai_grant_text(title=title, summary=summary, description=description) and "AI" not in out:
        out.append("AI")
    return out


def append_video_tag_if_matched(tags: list, title: str = "", summary: str = "", description: str = "") -> list:
    out = list(dict.fromkeys(t for t in tags if t))
    if is_video_grant_text(title=title, summary=summary, description=description) and "影片" not in out:
        out.append("影片")
    return out


def append_culture_tag_if_matched(tags: list, title: str = "", summary: str = "", description: str = "") -> list:
    out = list(dict.fromkeys(t for t in tags if t))
    if is_culture_grant_text(title=title, summary=summary, description=description) and "文化" not in out:
        out.append("文化")
    return out


def append_education_tag_if_matched(tags: list, title: str = "", summary: str = "", description: str = "") -> list:
    out = list(dict.fromkeys(t for t in tags if t))
    if is_education_grant_text(title=title, summary=summary, description=description) and "教育" not in out:
        out.append("教育")
    return out


def append_grant_category_tags_if_matched(
    tags: list,
    title: str = "",
    summary: str = "",
    description: str = "",
) -> list:
    out = append_ai_tag_if_matched(tags, title=title, summary=summary, description=description)
    out = append_video_tag_if_matched(out, title=title, summary=summary, description=description)
    out = append_culture_tag_if_matched(out, title=title, summary=summary, description=description)
    return append_education_tag_if_matched(out, title=title, summary=summary, description=description)


def crawl_startup_sme(max_pages: int = 40, progress=None) -> dict:
    """抓新創圓夢網政府資源總覽（分頁），解析每張 planCard。

    結構（2026-06 實測）：
      div.planCard
        .planCard__type span            -> 資源類型（資金/補助/…）
        ul.planCard__hashtags a.hashtags -> 關鍵字標籤
        a.planCard__link[href][title]    -> 詳情連結與標題
        .cardTitle span                  -> 標題
        .mainInfo                        -> 說明
    這些多為常設方案，列表頁不含申請截止日，apply_end 留空。
    """
    from bs4 import BeautifulSoup

    def log(m):
        if progress:
            progress(m)

    stats = {"inserted": 0, "updated": 0, "skipped": 0, "parsed": 0, "pages": 0}
    with make_client(accept="text/html,application/xhtml+xml,*/*") as client, \
            db.get_conn() as conn:
        source_id = db.ensure_source(conn, "新創圓夢網-政府資源總覽", STARTUP_SME_LIST, "html")
        run_id = db.start_crawl_run(conn, source_id, note="startup_sme rproject")
        try:
            for page in range(1, max_pages + 1):
                url = f"{STARTUP_SME_LIST}?page={page}"
                resp = client.get(url)
                resp.raise_for_status()
                soup = BeautifulSoup(resp.text, "lxml")
                cards = soup.select("div.planCard")
                if not cards:
                    break
                stats["pages"] += 1
                log(f"  page {page}: {len(cards)} 筆")
                for card in cards:
                    stats["parsed"] += 1
                    link = card.select_one("a.planCard__link")
                    if not link:
                        stats["skipped"] += 1
                        continue
                    href = link.get("href", "")
                    full = urljoin(url, href)
                    title_el = card.select_one(".cardTitle span") or link
                    title = (title_el.get_text(strip=True)
                             or link.get("title", "")).strip()
                    rtype_el = card.select_one(".planCard__type span")
                    rtype = rtype_el.get_text(strip=True) if rtype_el else None
                    hashtags = [a.get_text(strip=True).lstrip("#").strip()
                                for a in card.select("ul.planCard__hashtags a.hashtags")]
                    hashtags = [t for t in hashtags if t]
                    tags = ([rtype] if rtype else []) + hashtags
                    info_el = card.select_one(".mainInfo")
                    desc = info_el.get_text(" ", strip=True) if info_el else ""
                    tags = append_grant_category_tags_if_matched(tags, title=title, description=desc)
                    row = {
                        "source_id": source_id,
                        "agency": "新創圓夢網（中小企業署彙整）",
                        "title": title,
                        "target": rtype,
                        "tags": json.dumps(tags, ensure_ascii=False) if tags else None,
                        "apply_start": None,
                        "apply_end": None,
                        "url": full,
                        "raw_json": json.dumps(
                            {"title": title, "type": rtype, "hashtags": hashtags,
                             "description": desc, "url": full},
                            ensure_ascii=False),
                    }
                    if not row["url"] or not title:
                        stats["skipped"] += 1
                        continue
                    stats[db.upsert_grant(conn, row)] += 1
                conn.commit()
                time.sleep(settings.CRAWL_DELAY)  # 對政府站客氣點
            db.mark_source_success(conn, source_id)
            db.finish_crawl_run(conn, run_id, "success", stats["inserted"] + stats["updated"])
        except Exception as e:  # noqa: BLE001
            db.finish_crawl_run(conn, run_id, "failed",
                                stats["inserted"] + stats["updated"], note=str(e))
            raise
    return stats


# ============================================================
# (A2) 已實作爬蟲：文化部獎補助資訊網（JSON API，含申請截止日）
# ============================================================
MOC_API = "https://grants.moc.gov.tw/Web/API/PointListData.jsp"
MOC_REFERER = "https://grants.moc.gov.tw/Web/PointList.jsp?typeKeyWord=all"
MOC_BASE = "https://grants.moc.gov.tw/Web/"
# DataStatus -> (說明, pointCountMap 的 key)；2026-06 實測
_MOC_TABS = {1: ("徵件中", "DataStatus4"), 2: ("即將截止", "DataStatus5"), 3: ("已截止", "DataStatus6")}


def crawl_moc_grants(max_pages: int = 50, include_expired: bool = False, progress=None) -> dict:
    """抓文化部獎補助資訊網。

    流程（2026-06 實測）：POST JSON 到 API/PointListData.jsp，
    body={OP:'pointList', perSize, offset, DataStatus, typeKeyWord:'all', ...}，
    回傳 {Result, HtmlContent(片段), pointCountMap}；不需 viewstate。
    HtmlContent 內每筆 li.list-group-item：
      a.text-decoration-underline -> 標題 + PointDetail.jsp 連結
      div.col-lg-2(第一個)        -> 適用對象
      time span.d-block           -> 申請期間「115/05/04 ~ 115/06/11」（民國年）
    預設只抓徵件中(1)+即將截止(2)；已截止(3)有 2466 筆歷史封存，需 include_expired。
    """
    from bs4 import BeautifulSoup

    def log(m):
        if progress:
            progress(m)

    stats = {"inserted": 0, "updated": 0, "skipped": 0, "parsed": 0, "pages": 0}
    statuses = [1, 2] + ([3] if include_expired else [])
    per = 50
    with make_client(referer=MOC_REFERER, accept="application/json, */*") as client, \
            db.get_conn() as conn:
        source_id = db.ensure_source(conn, "文化部獎補助資訊網", MOC_BASE, "api")
        run_id = db.start_crawl_run(conn, source_id, note=f"moc statuses={statuses}")
        try:
            for ds in statuses:
                offset = 0
                for _ in range(max_pages):
                    body = {"OP": "pointList", "perSize": per, "offset": offset,
                            "DataStatus": ds, "keyWord": "", "unitKeyWord": "",
                            "typeKeyWord": "all", "onlineStatus": "", "orderType": "",
                            "targetVal": "", "startDate": "", "endDate": ""}
                    resp = client.post(MOC_API, json=body)
                    resp.raise_for_status()
                    data = resp.json()
                    if str(data.get("Result")) != "Success":
                        break
                    soup = BeautifulSoup(data.get("HtmlContent") or "", "lxml")
                    items = soup.select("li.list-group-item")
                    if not items:
                        break
                    stats["pages"] += 1
                    log(f"  文化部[{_MOC_TABS[ds][0]}] offset {offset}: {len(items)} 筆")
                    for li in items:
                        stats["parsed"] += 1
                        a = li.select_one("a.text-decoration-underline[href]")
                        if not a:
                            stats["skipped"] += 1
                            continue
                        title = a.get_text(strip=True)
                        url = urljoin(MOC_BASE, a.get("href", ""))
                        target_el = li.select_one("div.col-lg-2")
                        target = target_el.get_text(strip=True) if target_el else None
                        apply_start = apply_end = None
                        time_el = li.select_one("time span.d-block")
                        if time_el and "~" in time_el.get_text():
                            s, e = time_el.get_text(strip=True).split("~", 1)
                            apply_start, _ = roc_to_iso(s.strip())
                            apply_end, _ = roc_to_iso(e.strip())
                        if not title or not url:
                            stats["skipped"] += 1
                            continue
                        tags = append_grant_category_tags_if_matched(["文化觀光"], title=title)
                        row = {
                            "source_id": source_id,
                            "agency": "文化部",
                            "title": title,
                            "target": target,
                            "tags": json.dumps(tags, ensure_ascii=False),
                            "apply_start": apply_start,
                            "apply_end": apply_end,
                            "url": url,
                            "raw_json": json.dumps(
                                {"title": title, "target": target, "status": _MOC_TABS[ds][0],
                                 "apply_start": apply_start, "apply_end": apply_end, "url": url},
                                ensure_ascii=False),
                        }
                        stats[db.upsert_grant(conn, row)] += 1
                    conn.commit()
                    offset += per
                    cnt = data.get("pointCountMap", {}).get(_MOC_TABS[ds][1])
                    time.sleep(settings.CRAWL_DELAY)
                    if cnt is not None and offset >= int(cnt):
                        break
            db.mark_source_success(conn, source_id)
            db.finish_crawl_run(conn, run_id, "success", stats["inserted"] + stats["updated"])
        except Exception as e:  # noqa: BLE001
            db.finish_crawl_run(conn, run_id, "failed",
                                stats["inserted"] + stats["updated"], note=str(e))
            raise
    return stats


# ============================================================
# (A3) Additional grant sources: API/static HTML only
# ============================================================
DIGIPLUS_API_BASE = "https://digiplus.adi.gov.tw/api/v1/"
DIGIPLUS_BASE = "https://digiplus.adi.gov.tw/"
NCAF_FOUNDING_URL = "https://www.ncafroc.org.tw/founding.html"
TAOYUAN_SBIR_URL = "https://taoyuan-sbir.tw/"
KH_SBIR_URL = "https://kh-sbir.kcg.gov.tw/"

COVERAGE_BLOCKED = {
    "grb": "Playwright headed probe timed out at https://www.grb.gov.tw/ before DOMContentLoaded; GRB remains a research project/result database, not a current grant-call feed.",
    "new_taipei_sbir": "No official listable non-social current page confirmed in this pass",
}


def _decode_digiplus_html(item: dict) -> str:
    value = item.get("contendStr") or ""
    if value:
        return unquote(value)
    value = item.get("contend") or ""
    if not value:
        return ""
    try:
        return unquote(base64.b64decode(value).decode("utf-8"))
    except Exception:  # noqa: BLE001
        return ""


def _text_from_html(value: str) -> str:
    from bs4 import BeautifulSoup

    if not value:
        return ""
    return html.unescape(BeautifulSoup(value, "lxml").get_text(" ", strip=True))


def _extract_apply_end(text: str) -> Optional[str]:
    if not text:
        return None
    patterns = [
        r"(?:申請至|收件至|報名至|截止(?:日|時間)?(?:至|為)?|至)\s*(\d{2,3})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日",
        r"(?:申請至|收件至|報名至|截止(?:日|時間)?(?:至|為)?|至)\s*(\d{2,3})[/-](\d{1,2})[/-](\d{1,2})",
        r"(\d{2,3})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日.{0,12}(?:截止|止)",
    ]
    for pat in patterns:
        m = re.search(pat, text)
        if not m:
            continue
        iso, _ = roc_to_iso("/".join(m.groups()))
        if iso:
            return iso
    return None


def _grantish(title: str, desc: str = "") -> bool:
    text = f"{title}\n{desc}"
    return any(k in text for k in ("補助", "獎勵", "獎助", "申請", "徵案", "徵件", "SBIR"))


def crawl_digiplus(max_pages: int = 5, progress=None) -> dict:
    """DIGITAL+ API crawler, including the 115 AI innovation service R&D grant."""
    def log(m):
        if progress:
            progress(m)

    stats = {"inserted": 0, "updated": 0, "skipped": 0, "parsed": 0, "pages": 0}
    seen = set()
    with make_client(referer=DIGIPLUS_BASE, accept="application/json, */*") as client, \
            db.get_conn() as conn:
        source_id = db.ensure_source(conn, "DIGITAL+ digital innovation grants", DIGIPLUS_BASE, "api")
        run_id = db.start_crawl_run(conn, source_id, note="digiplus api")
        try:
            items = []
            for plan_classfy in ("1", "2"):
                resp = client.get(urljoin(DIGIPLUS_API_BASE, "indexNew/searchIconPic"),
                                  params={"planClassfy": plan_classfy})
                resp.raise_for_status()
                items.extend(resp.json() or [])
            per = 20
            for page in range(max_pages):
                ao_data = [
                    {"name": "iDisplayStart", "value": page * per},
                    {"name": "iDisplayLength", "value": per},
                    {"name": "classfy", "value": "0"},
                    {"name": "sortName", "value": "showTime"},
                    {"name": "sortValue", "value": "desc"},
                ]
                resp = client.get(urljoin(DIGIPLUS_API_BASE, "indexNew/searchPageNewsNew"),
                                  params={"aoData": json.dumps(ao_data)})
                resp.raise_for_status()
                data = resp.json() or {}
                batch = data.get("aaData") or []
                if not batch:
                    break
                items.extend(batch)
                stats["pages"] += 1
                if len(batch) < per:
                    break
            for item in items:
                item_id = item.get("id")
                if not item_id or item_id in seen:
                    continue
                seen.add(item_id)
                detail = item
                if not item.get("contendStr"):
                    resp = client.get(urljoin(DIGIPLUS_API_BASE, "indexNew/searchById"),
                                      params={"id": item_id})
                    resp.raise_for_status()
                    detail = resp.json() or item
                title = (detail.get("title") or item.get("title") or "").strip()
                desc = _text_from_html(_decode_digiplus_html(detail))
                if not title or not _grantish(title, desc):
                    stats["skipped"] += 1
                    continue
                stats["parsed"] += 1
                url = urljoin(DIGIPLUS_BASE, f"plan_table_newinner.html?id={item_id}")
                if detail.get("classfy") == "0":
                    url = urljoin(DIGIPLUS_BASE, f"news_listinner.html?id={item_id}")
                target = detail.get("planItemName") or detail.get("stageName") or detail.get("planClassfyName")
                tags = [t for t in [
                    "DIGITAL+",
                    detail.get("planClassfyName"),
                    detail.get("planItemName"),
                ] if t]
                tags = append_grant_category_tags_if_matched(tags, title=title, description=desc)
                row = {
                    "source_id": source_id,
                    "agency": "數位發展部數位產業署",
                    "title": title,
                    "target": target,
                    "tags": json.dumps(tags, ensure_ascii=False),
                    "apply_start": normalize_iso(detail.get("showTimed")),
                    "apply_end": _extract_apply_end(f"{title}\n{desc}"),
                    "url": url,
                    "raw_json": json.dumps(
                        {"id": item_id, "title": title, "target": target,
                         "description": desc[:2000], "source": detail},
                        ensure_ascii=False),
                }
                stats[db.upsert_grant(conn, row)] += 1
            conn.commit()
            log(f"  digiplus: parsed {stats['parsed']} grant-like rows")
            db.mark_source_success(conn, source_id)
            db.finish_crawl_run(conn, run_id, "success", stats["inserted"] + stats["updated"])
        except Exception as e:  # noqa: BLE001
            db.finish_crawl_run(conn, run_id, "failed",
                                stats["inserted"] + stats["updated"], note=str(e))
            raise
    return stats


def crawl_ncaf(max_pages: int = 1, progress=None) -> dict:
    from bs4 import BeautifulSoup

    stats = {"inserted": 0, "updated": 0, "skipped": 0, "parsed": 0, "pages": 0}
    with make_client(accept="text/html,application/xhtml+xml,*/*") as client, \
            db.get_conn() as conn:
        source_id = db.ensure_source(conn, "National Culture and Arts Foundation grants",
                                     NCAF_FOUNDING_URL, "html")
        run_id = db.start_crawl_run(conn, source_id, note="ncaf founding")
        try:
            resp = client.get(NCAF_FOUNDING_URL)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "lxml")
            stats["pages"] = 1
            for a in soup.select('a[href*="founding_detail.html"]'):
                text = a.get_text(" ", strip=True)
                href = urljoin(NCAF_FOUNDING_URL, a.get("href", ""))
                if not text or not href:
                    stats["skipped"] += 1
                    continue
                stats["parsed"] += 1
                title = re.sub(r"\s*(收件中|開放收件時間未定).*", "", text).strip() or text
                tags = append_grant_category_tags_if_matched(["文化", "藝文補助"], title=title, description=text)
                row = {
                    "source_id": source_id,
                    "agency": "國家文化藝術基金會",
                    "title": title,
                    "target": "文化藝術",
                    "tags": json.dumps(tags, ensure_ascii=False),
                    "apply_start": None,
                    "apply_end": _extract_apply_end(text),
                    "url": href,
                    "raw_json": json.dumps({"title": title, "list_text": text, "url": href},
                                           ensure_ascii=False),
                }
                stats[db.upsert_grant(conn, row)] += 1
            conn.commit()
            db.mark_source_success(conn, source_id)
            db.finish_crawl_run(conn, run_id, "success", stats["inserted"] + stats["updated"])
        except Exception as e:  # noqa: BLE001
            db.finish_crawl_run(conn, run_id, "failed",
                                stats["inserted"] + stats["updated"], note=str(e))
            raise
    return stats


def crawl_taoyuan_sbir(max_pages: int = 1, progress=None) -> dict:
    from bs4 import BeautifulSoup

    stats = {"inserted": 0, "updated": 0, "skipped": 0, "parsed": 0, "pages": 0}
    with make_client(accept="text/html,application/xhtml+xml,*/*") as client, \
            db.get_conn() as conn:
        source_id = db.ensure_source(conn, "Taoyuan local SBIR", TAOYUAN_SBIR_URL, "html")
        run_id = db.start_crawl_run(conn, source_id, note="taoyuan sbir")
        try:
            resp = client.get(TAOYUAN_SBIR_URL)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "lxml")
            page_text = soup.get_text(" ", strip=True)
            title = "115年桃園市地方型SBIR"
            h = soup.find(string=re.compile(r"115.*SBIR|桃園.*SBIR"))
            if h:
                title = h.strip()
            stats.update({"pages": 1, "parsed": 1})
            tags = append_grant_category_tags_if_matched(["地方型SBIR", "研發補助"], title=title,
                                                         description=page_text)
            row = {
                "source_id": source_id,
                "agency": "桃園市政府",
                "title": title,
                "target": "桃園市中小企業",
                "tags": json.dumps(tags, ensure_ascii=False),
                "apply_start": None,
                "apply_end": _extract_apply_end(page_text),
                "url": TAOYUAN_SBIR_URL,
                "raw_json": json.dumps({"title": title, "description": page_text[:2000],
                                        "url": TAOYUAN_SBIR_URL}, ensure_ascii=False),
            }
            stats[db.upsert_grant(conn, row)] += 1
            conn.commit()
            db.mark_source_success(conn, source_id)
            db.finish_crawl_run(conn, run_id, "success", stats["inserted"] + stats["updated"])
        except Exception as e:  # noqa: BLE001
            db.finish_crawl_run(conn, run_id, "failed",
                                stats["inserted"] + stats["updated"], note=str(e))
            raise
    return stats


def crawl_kh_sbir(max_pages: int = 1, progress=None) -> dict:
    from bs4 import BeautifulSoup

    stats = {"inserted": 0, "updated": 0, "skipped": 0, "parsed": 0, "pages": 0}
    with make_client(accept="text/html,application/xhtml+xml,*/*") as client, \
            db.get_conn() as conn:
        source_id = db.ensure_source(conn, "Kaohsiung local SBIR", KH_SBIR_URL, "html")
        run_id = db.start_crawl_run(conn, source_id, note="kh sbir")
        try:
            resp = client.get(KH_SBIR_URL)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "lxml")
            page_text = soup.get_text(" ", strip=True)
            title = "115年度高雄市地方型SBIR"
            h = soup.find(string=re.compile(r"115.*(?:高雄|SBIR).*(?:申請|徵件|補助)"))
            if h:
                title = h.strip()
            stats.update({"pages": 1, "parsed": 1})
            tags = append_grant_category_tags_if_matched(["地方型SBIR", "研發補助"], title=title,
                                                         description=page_text)
            row = {
                "source_id": source_id,
                "agency": "高雄市政府",
                "title": title,
                "target": "高雄市中小企業",
                "tags": json.dumps(tags, ensure_ascii=False),
                "apply_start": None,
                "apply_end": _extract_apply_end(page_text),
                "url": KH_SBIR_URL,
                "raw_json": json.dumps({"title": title, "description": page_text[:2000],
                                        "url": KH_SBIR_URL}, ensure_ascii=False),
            }
            stats[db.upsert_grant(conn, row)] += 1
            conn.commit()
            db.mark_source_success(conn, source_id)
            db.finish_crawl_run(conn, run_id, "success", stats["inserted"] + stats["updated"])
        except Exception as e:  # noqa: BLE001
            db.finish_crawl_run(conn, run_id, "failed",
                                stats["inserted"] + stats["updated"], note=str(e))
            raise
    return stats


def coverage_map(results: Optional[dict] = None) -> dict:
    out = {}
    for name in HTML_CRAWLERS:
        if results and name in results:
            st = results[name]
            if isinstance(st, dict) and "error" not in st:
                out[name] = f"{st.get('inserted', 0) + st.get('updated', 0)} rows"
            elif isinstance(st, dict):
                out[name] = f"blocked/error: {st.get('error')}"
            else:
                out[name] = str(st)
        else:
            out[name] = "not run"
    out.update({name: f"blocked: {reason}" for name, reason in COVERAGE_BLOCKED.items()})
    return out


# 已實作的命名爬蟲（crawl_all 會全跑）
HTML_CRAWLERS: dict = {
    "startup_sme": crawl_startup_sme,
    "moc": crawl_moc_grants,
    "digiplus": crawl_digiplus,
    "ncaf": crawl_ncaf,
    "taoyuan_sbir": crawl_taoyuan_sbir,
    "kh_sbir": crawl_kh_sbir,
}


def crawl_all(progress=None) -> dict:
    """跑所有已實作的爬蟲，回傳各來源統計。"""
    out = {}
    for name, fn in HTML_CRAWLERS.items():
        if progress:
            progress(f"=== 抓取來源：{name} ===")
        try:
            out[name] = fn(progress=progress)
        except Exception as e:  # noqa: BLE001
            out[name] = {"error": str(e)}
            if progress:
                progress(f"  來源 {name} 失敗：{e}")
    return out


# ============================================================
# (B) 種子匯入：補上爬蟲涵蓋不到的部會
# ============================================================
def ingest_seed(seed_path: str) -> dict:
    """從 JSON 檔匯入補助清單。

    格式：[{"agency","title","target","tags":[...],
            "apply_start","apply_end","url","description"}, ...]
    日期可填 ISO / 民國年 / YYYYMMDD，會自動正規化。
    """
    p = Path(seed_path)
    if not p.exists():
        raise FileNotFoundError(seed_path)
    items = json.loads(p.read_text(encoding="utf-8"))
    stats = {"inserted": 0, "updated": 0, "skipped": 0}
    with db.get_conn() as conn:
        source_id = db.ensure_source(conn, "Grants seed", str(p), "manual")
        run_id = db.start_crawl_run(conn, source_id, note=f"seed={p.name}")
        try:
            for it in items:
                if not it.get("url"):
                    stats["skipped"] += 1
                    continue
                row = {
                    "source_id": source_id,
                    "agency": it.get("agency"),
                    "title": it.get("title"),
                    "target": it.get("target"),
                    "tags": json.dumps(it.get("tags", []), ensure_ascii=False) if it.get("tags") else None,
                    "apply_start": normalize_iso(it.get("apply_start")),
                    "apply_end": normalize_iso(it.get("apply_end")),
                    "url": it.get("url"),
                    "raw_json": json.dumps(it, ensure_ascii=False),
                }
                stats[db.upsert_grant(conn, row)] += 1
            db.mark_source_success(conn, source_id)
            db.finish_crawl_run(conn, run_id, "success", stats["inserted"] + stats["updated"])
        except Exception as e:  # noqa: BLE001
            db.finish_crawl_run(conn, run_id, "failed",
                                stats["inserted"] + stats["updated"], note=str(e))
            raise
    return stats


# ============================================================
# (C) 通用 HTML 來源骨架：接新部會用（填好 selector 即可）
# ============================================================
@dataclass
class HtmlSource:
    name: str
    list_url: str
    item_selector: str = ""
    title_selector: str = "a"
    link_selector: str = "a"
    link_attr: str = "href"
    base_url: str = ""
    parse_item: Optional[Callable] = None


def crawl_html(source: HtmlSource, limit: int = 50) -> dict:
    from bs4 import BeautifulSoup

    stats = {"inserted": 0, "updated": 0, "skipped": 0, "parsed": 0}
    with make_client(accept="text/html,application/xhtml+xml,*/*") as client, \
            db.get_conn() as conn:
        source_id = db.ensure_source(conn, source.name, source.list_url, "html")
        run_id = db.start_crawl_run(conn, source_id, note="html crawl")
        try:
            resp = client.get(source.list_url)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "lxml")
            if not source.item_selector:
                raise ValueError(f"來源 {source.name} 未設定 item_selector")
            for el in soup.select(source.item_selector)[:limit]:
                stats["parsed"] += 1
                item = source.parse_item(el) if source.parse_item else _default_parse(el, source)
                if not item or not item.get("url"):
                    stats["skipped"] += 1
                    continue
                row = {
                    "source_id": source_id,
                    "agency": item.get("agency") or source.name,
                    "title": item.get("title"),
                    "target": item.get("target"),
                    "tags": json.dumps(item.get("tags", []), ensure_ascii=False) if item.get("tags") else None,
                    "apply_start": normalize_iso(item.get("apply_start")),
                    "apply_end": normalize_iso(item.get("apply_end")),
                    "url": item.get("url"),
                    "raw_json": json.dumps(item, ensure_ascii=False),
                }
                stats[db.upsert_grant(conn, row)] += 1
            db.mark_source_success(conn, source_id)
            db.finish_crawl_run(conn, run_id, "success", stats["inserted"] + stats["updated"])
        except Exception as e:  # noqa: BLE001
            db.finish_crawl_run(conn, run_id, "failed",
                                stats["inserted"] + stats["updated"], note=str(e))
            raise
    return stats


def _default_parse(el, source: HtmlSource) -> Optional[dict]:
    title_el = el.select_one(source.title_selector)
    link_el = el.select_one(source.link_selector)
    if not link_el:
        return None
    href = link_el.get(source.link_attr, "")
    if href and source.base_url:
        href = urljoin(source.base_url, href)
    return {
        "title": (title_el.get_text(strip=True) if title_el else link_el.get_text(strip=True)),
        "url": href,
    }
