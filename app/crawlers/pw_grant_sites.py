"""Playwright crawlers for grant pages that fail or degrade under plain httpx."""
import json
import re
from pathlib import Path
from urllib.parse import urljoin

from playwright.sync_api import sync_playwright

from .. import db
from ..config import settings
from ..roc_date import roc_to_iso
from . import grants as grant_helpers


SBIR_LOCALCITY_URL = "https://sbir.org.tw/localcity"
NSTC_RFP_URL = "https://www.nstc.gov.tw/folksonomy/rfpList?l=ch"
TAIPEI_SITI_URL = "https://www.industry-incentive.taipei/"
TAICHUNG_SBIR_URL = "https://www.economic.taichung.gov.tw/16103/16107/16114/3274817"
TAINAN_SBIR_URL = "https://www.tainan-sbir.org.tw/"


def _profile(name: str) -> str:
    return str(Path(settings.DB_PATH).resolve().parent / f".pw_{name}")


def _stats() -> dict:
    return {"inserted": 0, "updated": 0, "skipped": 0, "parsed": 0, "pages": 0}


def _extract_roc_range(text: str) -> tuple[str | None, str | None]:
    if not text:
        return None, None
    normalized = re.sub(r"\s+", "", text)
    patterns = [
        r"(\d{2,3})年(\d{1,2})月(\d{1,2})日(?:[^0-9]{0,20})(?:至|~|起至)(\d{2,3})年(\d{1,2})月(\d{1,2})日",
        r"(\d{2,3})[/-](\d{1,2})[/-](\d{1,2})(?:[^0-9]{0,20})(?:至|~|起至)(\d{2,3})[/-](\d{1,2})[/-](\d{1,2})",
    ]
    for pattern in patterns:
        match = re.search(pattern, normalized)
        if not match:
            continue
        a, b, c, d, e, f = match.groups()
        start, _ = roc_to_iso(f"{a}/{b}/{c}")
        end, _ = roc_to_iso(f"{d}/{e}/{f}")
        return start, end
    end_patterns = [
        r"(?:至|截止|止)(\d{2,3})年(\d{1,2})月(\d{1,2})日",
        r"(\d{2,3})年(\d{1,2})月(\d{1,2})日(?:[^。；，,]{0,20})(?:截止|止)",
    ]
    for pattern in end_patterns:
        match = re.search(pattern, normalized)
        if match:
            end, _ = roc_to_iso("/".join(match.groups()))
            return None, end
    return None, None


def _tagged(tags: list[str], *, title: str, description: str = "") -> str:
    out = grant_helpers.append_grant_category_tags_if_matched(
        tags,
        title=title,
        description=description,
    )
    return json.dumps(out, ensure_ascii=False)


def _upsert_rows(source_name: str, base_url: str, strategy: str, note: str, rows: list[dict]) -> dict:
    stats = _stats()
    with db.get_conn() as conn:
        source_id = db.ensure_source(conn, source_name, base_url, strategy)
        run_id = db.start_crawl_run(conn, source_id, note=note)
        try:
            for row in rows:
                if not row.get("title") or not row.get("url"):
                    stats["skipped"] += 1
                    continue
                stats["parsed"] += 1
                row["source_id"] = source_id
                stats[db.upsert_grant(conn, row)] += 1
            conn.commit()
            db.mark_source_success(conn, source_id)
            db.finish_crawl_run(conn, run_id, "success", stats["inserted"] + stats["updated"])
        except Exception as exc:  # noqa: BLE001
            db.finish_crawl_run(conn, run_id, "failed", stats["inserted"] + stats["updated"], note=str(exc))
            raise
    return stats


def _with_page(profile_name: str, headless: bool, callback):
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=_profile(profile_name),
            channel="chrome",
            headless=headless,
            locale="zh-TW",
            viewport={"width": 1360, "height": 900},
        )
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            return callback(page)
        finally:
            ctx.close()


def crawl_sbir_localcity(max_pages: int = 1, headless: bool = False, progress=None) -> dict:
    """Crawl the official SBIR local-city portal and create one row per linked city plan."""
    def work(page):
        page.goto(SBIR_LOCALCITY_URL, wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(2500)
        body = page.locator("body").inner_text(timeout=10000)
        links = page.eval_on_selector_all(
            "a[href]",
            """els => els.map(a => ({
                text: (a.innerText || a.textContent || a.title || '').trim(),
                href: a.href,
                title: a.title || ''
            }))""",
        )
        rows = []
        city_names = []
        for item in links:
            text = (item.get("text") or "").strip()
            href = (item.get("href") or "").strip()
            if text.endswith("在地特色產業"):
                city_names.append(text.replace("在地特色產業", ""))
            if text != "計畫連結" or not href.startswith("http"):
                continue
            city = city_names[len(rows)] if len(city_names) > len(rows) else "縣市"
            title = f"115年度{city}地方產業創新研發推動計畫（地方型SBIR）"
            rows.append({
                "agency": f"{city}政府",
                "title": title,
                "target": "地方中小企業創新研發",
                "tags": _tagged(["地方型SBIR", "研發補助"], title=title, description=body),
                "apply_start": None,
                "apply_end": None,
                "url": href,
                "raw_json": json.dumps({"city": city, "source": SBIR_LOCALCITY_URL, "url": href}, ensure_ascii=False),
            })
        if progress:
            progress(f"  sbir_localcity: {len(rows)} linked city plans")
        stats = _upsert_rows("SBIR local-city portal", SBIR_LOCALCITY_URL, "playwright", "sbir localcity", rows)
        stats["pages"] = max_pages
        return stats

    return _with_page("sbir_localcity", headless, work)


def crawl_nstc_rfp(max_pages: int = 1, headless: bool = False, progress=None) -> dict:
    """Crawl NSTC public RFP list."""
    def work(page):
        page.goto(NSTC_RFP_URL, wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(2500)
        items = page.eval_on_selector_all(
            "table tbody tr",
            """rows => rows.map(tr => {
                const cells = Array.from(tr.querySelectorAll('td')).map(td => td.innerText.trim());
                const detail = tr.querySelector('a.show_detail[href*="rfpDetail"]');
                const a = detail || tr.querySelector('a[data-target-url*="rfpDetail"]') || tr.querySelector('a[href*="rfpDetail"]');
                const rawHref = a ? (a.getAttribute('href') || a.dataset.targetUrl || '') : '';
                return {cells, title: a ? a.innerText.trim() : (cells[0] || ''), href: rawHref};
            })""",
        )
        if not items:
            items = page.eval_on_selector_all(
                "a[href*='rfpDetail']",
                "els => els.map(a => ({title: a.innerText.trim(), href: a.href, cells: []}))",
            )
        rows = []
        for item in items:
            title = (item.get("title") or "").strip()
            href = urljoin(NSTC_RFP_URL, (item.get("href") or "").strip())
            cells = item.get("cells") or []
            if not title or "rfpDetail" not in href:
                continue
            text = " ".join(cells)
            apply_start, apply_end = _extract_roc_range(text)
            target = cells[4] if len(cells) > 4 else None
            rows.append({
                "agency": "國家科學及技術委員會",
                "title": title,
                "target": target,
                "tags": _tagged(["國科會", "計畫徵求"], title=title, description=text),
                "apply_start": apply_start,
                "apply_end": apply_end,
                "url": href,
                "raw_json": json.dumps({"cells": cells, "source": NSTC_RFP_URL}, ensure_ascii=False),
            })
        if progress:
            progress(f"  nstc_rfp: {len(rows)} RFP rows")
        stats = _upsert_rows("NSTC public RFP list", NSTC_RFP_URL, "playwright", "nstc rfpList", rows)
        stats["pages"] = max_pages
        return stats

    return _with_page("nstc_rfp", headless, work)


def crawl_taipei_siti(max_pages: int = 1, headless: bool = False, progress=None) -> dict:
    """Crawl Taipei SITI official incentive site as standing grant programs."""
    def work(page):
        page.goto(TAIPEI_SITI_URL, wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(2500)
        body = page.locator("body").inner_text(timeout=10000)
        programs = ["創業補助", "研發補助", "品牌建立補助", "新創拔尖補助", "創新育成補助", "獎勵補貼"]
        rows = []
        for program in programs:
            title = f"臺北市產業發展獎勵補助計畫-{program}"
            rows.append({
                "agency": "臺北市政府產業發展局",
                "title": title,
                "target": "設立於臺北市之企業或新創團隊",
                "tags": _tagged(["SITI", "臺北市", program], title=title, description=body),
                "apply_start": None,
                "apply_end": None,
                "url": urljoin(TAIPEI_SITI_URL, f"#{program}"),
                "raw_json": json.dumps({"program": program, "source": TAIPEI_SITI_URL, "page_text": body[:3000]}, ensure_ascii=False),
            })
        if progress:
            progress(f"  taipei_siti: {len(rows)} standing program rows")
        stats = _upsert_rows("Taipei SITI industry incentives", TAIPEI_SITI_URL, "playwright", "taipei siti", rows)
        stats["pages"] = max_pages
        return stats

    return _with_page("taipei_siti", headless, work)


def crawl_taichung_sbir(max_pages: int = 1, headless: bool = False, progress=None) -> dict:
    def work(page):
        page.goto(TAICHUNG_SBIR_URL, wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(2500)
        body = page.locator("body").inner_text(timeout=10000)
        title = "115年度臺中市地方產業創新研發推動計畫(地方型SBIR)"
        apply_start, apply_end = _extract_roc_range(body)
        row = {
            "agency": "臺中市政府經濟發展局",
            "title": title,
            "target": "臺中市中小企業",
            "tags": _tagged(["地方型SBIR", "臺中市", "研發補助"], title=title, description=body),
            "apply_start": apply_start,
            "apply_end": apply_end,
            "url": TAICHUNG_SBIR_URL,
            "raw_json": json.dumps({"source": TAICHUNG_SBIR_URL, "page_text": body[:4000]}, ensure_ascii=False),
        }
        stats = _upsert_rows("Taichung local SBIR", TAICHUNG_SBIR_URL, "playwright", "taichung sbir", [row])
        stats["pages"] = max_pages
        if progress:
            progress("  taichung_sbir: 1 row")
        return stats

    return _with_page("taichung_sbir", headless, work)


def crawl_tainan_sbir(max_pages: int = 1, headless: bool = False, progress=None) -> dict:
    def work(page):
        page.goto(TAINAN_SBIR_URL, wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(2500)
        body = page.locator("body").inner_text(timeout=10000)
        title = "115年度臺南市地方型SBIR計畫"
        apply_start, apply_end = _extract_roc_range(body)
        row = {
            "agency": "臺南市政府經濟發展局",
            "title": title,
            "target": "設籍於臺南市的中小企業",
            "tags": _tagged(["地方型SBIR", "臺南市", "研發補助"], title=title, description=body),
            "apply_start": apply_start,
            "apply_end": apply_end,
            "url": TAINAN_SBIR_URL,
            "raw_json": json.dumps({"source": TAINAN_SBIR_URL, "page_text": body[:4000]}, ensure_ascii=False),
        }
        stats = _upsert_rows("Tainan local SBIR", TAINAN_SBIR_URL, "playwright", "tainan sbir", [row])
        stats["pages"] = max_pages
        if progress:
            progress("  tainan_sbir: 1 row")
        return stats

    return _with_page("tainan_sbir", headless, work)


PW_GRANT_CRAWLERS = {
    "sbir_localcity": crawl_sbir_localcity,
    "nstc_rfp": crawl_nstc_rfp,
    "taipei_siti": crawl_taipei_siti,
    "taichung_sbir": crawl_taichung_sbir,
    "tainan_sbir": crawl_tainan_sbir,
}


BLOCKED_SITES = {
    "new_taipei_sbir": "SBIR local-city official page states Taipei City and New Taipei City did not apply for 115 local SBIR; no official New Taipei local-SBIR grant row to ingest.",
    "grb": "Playwright headed probe to https://www.grb.gov.tw/ timed out at 45s before DOMContentLoaded; search results indicate GRB is a research project/result database, not a current grant-call feed.",
}
