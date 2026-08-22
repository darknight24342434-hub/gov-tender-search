"""經濟部補助計畫入口網爬蟲（Playwright + 真實 Chrome）。

這站在 Cloudflare 後面（「Attention Required」挑戰）。實測（2026-06）：
- headless 會被 CF 擋；**headed（有頭）真實 Chrome（channel="chrome"）可穩定通過**。
- 用 launch_persistent_context 存住 cf_clearance cookie（data/.pw_moea），後續更順。
- 清單頁 /EE502/NewPortal/Plan/Plan 以 AJAX 載入，共約 30 筆、每頁 10 筆、3 頁。
  每筆 div.content_block：
    a.md-trigger > h1                 -> 標題
    div[data-id^="Compared_"]         -> 計畫代碼（TIPO08/BSMI07/TRADE08…）
    .list_field                       -> 領域
    .list_schedule 「計畫期程：A ~ B」 -> 期程（民國年 → 西元）

需要 playwright 套件 + 已安裝的 Google Chrome。不放進 crawl_all（會彈出瀏覽器視窗）。
"""
import json
import re

from .. import db
from ..roc_date import roc_to_iso

PORTAL = "https://service.moea.gov.tw/EE502/NewPortal"
LIST_URL = f"{PORTAL}/Plan/Plan"

# 計畫代碼前綴 -> 主管機關（部分已知，其餘歸「經濟部」）
_UNIT_BY_PREFIX = {
    "TIPO": "經濟部智慧財產局", "BSMI": "經濟部標準檢驗局",
    "TRADE": "經濟部國際貿易署", "EPZA": "經濟部產業園區管理局",
    "W": "經濟部水利署", "WRA": "經濟部水利署",
    "IDB": "經濟部產業發展署", "IDA": "經濟部產業發展署",
    "SME": "經濟部中小及新創企業署", "MOEA": "經濟部",
}


def _agency(code: str) -> str:
    m = re.match(r"^[A-Za-z]+", code or "")
    return _UNIT_BY_PREFIX.get(m.group(0).upper(), "經濟部") if m else "經濟部"


def _wait_pass(pg, tries: int = 12) -> bool:
    for _ in range(tries):
        pg.wait_for_timeout(2500)
        t = pg.title()
        if "Attention" not in t and "Just a moment" not in t and t.strip():
            return True
    return False


def _parse_page(html: str) -> list:
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "lxml")
    out = []
    for block in soup.select("div.content_block"):
        h1 = block.select_one("a.md-trigger h1") or block.select_one("h1")
        if not h1:
            continue
        title = h1.get_text(strip=True)
        code_el = block.select_one('div[data-id^="Compared_"]')
        code = (code_el.get("data-id", "").replace("Compared_", "")
                if code_el else "")
        field_el = block.select_one(".list_field")
        field = field_el.get_text(strip=True) if field_el else None
        sched_el = block.select_one(".list_schedule")
        apply_start = apply_end = None
        if sched_el:
            sched = sched_el.get_text(strip=True)
            if "：" in sched:
                sched = sched.split("：", 1)[1]
            if "~" in sched:
                s, e = sched.split("~", 1)
                apply_start, _ = roc_to_iso(s.strip())
                apply_end, _ = roc_to_iso(e.strip())
        if not title or not code:
            continue
        out.append({"title": title, "code": code, "field": field,
                    "apply_start": apply_start, "apply_end": apply_end})
    return out


def _goto_page(pg, n: int) -> bool:
    for h in pg.query_selector_all(".pagination a"):
        try:
            if (h.inner_text() or "").strip() == str(n):
                h.click()
                pg.wait_for_timeout(2500)
                return True
        except Exception:  # noqa: BLE001
            continue
    return False


def crawl_moea(max_pages: int = 8, headless: bool = False, progress=None) -> dict:
    from pathlib import Path

    from playwright.sync_api import sync_playwright

    from ..config import settings

    def log(m):
        if progress:
            progress(m)

    profile = str(Path(settings.DB_PATH).resolve().parent / ".pw_moea")
    stats = {"inserted": 0, "updated": 0, "skipped": 0, "parsed": 0, "pages": 0}
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=profile, channel="chrome", headless=headless,
            locale="zh-TW", viewport={"width": 1360, "height": 900})
        try:
            pg = ctx.pages[0] if ctx.pages else ctx.new_page()
            pg.goto(LIST_URL, wait_until="domcontentloaded", timeout=45000)
            if not _wait_pass(pg):
                raise RuntimeError(f"無法通過 Cloudflare（title={pg.title()!r}）")
            pg.wait_for_selector("div.content_block a.md-trigger", timeout=20000)

            with db.get_conn() as conn:
                source_id = db.ensure_source(conn, "經濟部補助計畫入口網", LIST_URL, "playwright")
                run_id = db.start_crawl_run(conn, source_id, note="moea playwright")
                try:
                    seen = set()
                    for page_no in range(1, max_pages + 1):
                        html = pg.inner_html("#plan_filt_result")
                        items = _parse_page(html)
                        new_here = 0
                        for it in items:
                            if it["code"] in seen:
                                continue
                            seen.add(it["code"])
                            new_here += 1
                            stats["parsed"] += 1
                            row = {
                                "source_id": source_id,
                                "agency": _agency(it["code"]),
                                "title": it["title"],
                                "target": it.get("field"),
                                "tags": json.dumps([it["field"]], ensure_ascii=False) if it.get("field") else None,
                                "apply_start": it.get("apply_start"),
                                "apply_end": it.get("apply_end"),
                                "url": f"{LIST_URL}?planId={it['code']}",
                                "raw_json": json.dumps(it, ensure_ascii=False),
                            }
                            stats[db.upsert_grant(conn, row)] += 1
                        stats["pages"] += 1
                        log(f"  經濟部 page {page_no}: 本頁 {len(items)} 筆，新增 {new_here}")
                        conn.commit()
                        if new_here == 0 and page_no > 1:
                            break
                        if not _goto_page(pg, page_no + 1):
                            break
                    db.mark_source_success(conn, source_id)
                    db.finish_crawl_run(conn, run_id, "success",
                                        stats["inserted"] + stats["updated"])
                except Exception as e:  # noqa: BLE001
                    db.finish_crawl_run(conn, run_id, "failed",
                                        stats["inserted"] + stats["updated"], note=str(e))
                    raise
        finally:
            ctx.close()
    return stats
