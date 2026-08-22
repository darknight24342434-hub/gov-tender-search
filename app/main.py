"""FastAPI 主程式：密碼閘 + 搜尋 API + 詳情/摘要 + 單頁 UI。"""
import json
from pathlib import Path
from statistics import median
from typing import Optional

from fastapi import FastAPI, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import db, search
from .ai import codex_client, dissect as dissect_ai, summarize, tagging
from .auth import make_token, valid_token, verify_password
from .config import settings

BASE = Path(__file__).resolve().parent
app = FastAPI(title="政府標案 / 補助案搜尋")
templates = Jinja2Templates(directory=str(BASE / "templates"))
app.mount("/static", StaticFiles(directory=str(BASE / "static")), name="static")

PUBLIC_PATHS = {"/login", "/logout", "/healthz", "/favicon.ico"}


@app.on_event("startup")
def _startup():
    db.init_db()


# ---------- 密碼閘 ----------
@app.middleware("http")
async def auth_guard(request: Request, call_next):
    path = request.url.path
    if path.startswith("/static") or path in PUBLIC_PATHS:
        return await call_next(request)
    if valid_token(request.cookies.get(settings.COOKIE_NAME)):
        return await call_next(request)
    if path.startswith("/api"):
        return JSONResponse({"detail": "未授權，請重新登入"}, status_code=401)
    return RedirectResponse(url="/login", status_code=302)


@app.get("/healthz")
def healthz():
    return {"ok": True, "codex": codex_client.available()}


@app.get("/login")
def login_page(request: Request):
    no_pw = not settings.APP_PASSWORD
    return templates.TemplateResponse(
        request, "login.html", {"error": None, "no_password": no_pw}
    )


@app.post("/login")
def login_submit(request: Request, password: str = Form(...)):
    if verify_password(password):
        resp = RedirectResponse(url="/", status_code=302)
        resp.set_cookie(
            settings.COOKIE_NAME, make_token(),
            max_age=settings.SESSION_MAX_AGE, httponly=True,
            samesite="lax", secure=settings.COOKIE_SECURE,
        )
        return resp
    return templates.TemplateResponse(
        request,
        "login.html",
        {"error": "密碼錯誤", "no_password": not settings.APP_PASSWORD},
        status_code=401,
    )


@app.get("/logout")
def logout():
    resp = RedirectResponse(url="/login", status_code=302)
    resp.delete_cookie(settings.COOKIE_NAME)
    return resp


@app.get("/")
def index(request: Request):
    return templates.TemplateResponse(request, "index.html", {})


# ---------- 搜尋 API ----------
def _split_tags(tags: Optional[str]):
    if not tags:
        return []
    return [t.strip() for t in tags.split(",") if t.strip()]


@app.get("/api/meta/tags")
def meta_tags():
    with db.get_conn() as conn:
        return {"tags": db.list_tags(conn)}


@app.get("/api/search/tenders")
def api_search_tenders(
    q: Optional[str] = None,
    tags: Optional[str] = None,
    deadline_from: Optional[str] = None,
    deadline_to: Optional[str] = None,
    type: Optional[str] = None,
    only_active: bool = False,
    page: int = 1,
):
    with db.get_conn() as conn:
        return search.search_tenders(
            conn, q=q, tags=_split_tags(tags),
            deadline_from=deadline_from, deadline_to=deadline_to,
            type_=type, only_active=only_active, page=page,
        )


@app.get("/api/search/grants")
def api_search_grants(
    q: Optional[str] = None,
    tags: Optional[str] = None,
    target: Optional[str] = None,
    deadline_from: Optional[str] = None,
    only_active: bool = False,
    page: int = 1,
):
    with db.get_conn() as conn:
        return search.search_grants(
            conn, q=q, tags=_split_tags(tags), target=target,
            deadline_from=deadline_from, only_active=only_active, page=page,
        )


def _award_row(row) -> dict:
    item = dict(row)
    item.pop("raw_json", None)
    return item


def _award_summary(rows) -> dict:
    ratios = [float(r["ratio"]) for r in rows if r["ratio"] is not None]
    bidders = [int(r["bidders"]) for r in rows if r["bidders"] is not None]
    winners = {}
    for row in rows:
        winner = (row["winner"] or "").strip()
        if winner:
            winners[winner] = winners.get(winner, 0) + 1
    top_winners = [
        {"winner": winner, "count": count}
        for winner, count in sorted(winners.items(), key=lambda x: (-x[1], x[0]))[:5]
    ]
    return {
        "median_ratio": round(median(ratios), 3) if ratios else None,
        "avg_bidders": round(sum(bidders) / len(bidders), 2) if bidders else None,
        "single_bidder_count": sum(1 for n in bidders if n == 1),
        "top_winners": top_winners,
    }


@app.get("/api/awards/search")
def api_search_awards(
    q: Optional[str] = None,
    agency: Optional[str] = None,
    page: int = 1,
):
    page_size = 20
    where, params = [], []
    if q:
        like = f"%{q}%"
        where.append("(title LIKE ? OR agency LIKE ? OR winner LIKE ?)")
        params += [like, like, like]
    if agency:
        where.append("agency LIKE ?")
        params.append(f"%{agency}%")
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    offset = max(0, page - 1) * page_size
    with db.get_conn() as conn:
        total = conn.execute(f"SELECT COUNT(*) AS n FROM awards{clause}", params).fetchone()["n"]
        rows = conn.execute(
            f"SELECT * FROM awards{clause} "
            "ORDER BY award_date DESC, id DESC LIMIT ? OFFSET ?",
            (*params, page_size, offset),
        ).fetchall()
        summary_rows = conn.execute(f"SELECT * FROM awards{clause}", params).fetchall()
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [_award_row(r) for r in rows],
        "summary": _award_summary(summary_rows),
    }


@app.get("/api/tenders/{tid}")
def api_tender(tid: int):
    with db.get_conn() as conn:
        row = search.get_one(conn, "tenders", tid)
    if not row:
        return JSONResponse({"detail": "查無此標案"}, status_code=404)
    return row


@app.get("/api/grants/{gid}")
def api_grant(gid: int):
    with db.get_conn() as conn:
        row = search.get_one(conn, "grants", gid)
    if not row:
        return JSONResponse({"detail": "查無此補助案"}, status_code=404)
    return row


# ---------- AI 摘要（詳情頁觸發；有快取就不重打，省 Codex 額度）----------
@app.post("/api/tenders/{tid}/summarize")
def summarize_tender(tid: int, force: bool = False):
    with db.get_conn() as conn:
        row = search.get_one(conn, "tenders", tid)
        if not row:
            return JSONResponse({"detail": "查無此標案"}, status_code=404)
        if row.get("summary") and not force:
            return {"summary": row["summary"], "cached": True}
        result = summarize.summarize(summarize.tender_body(row), kind="標案")
        if not result:
            return JSONResponse(
                {"detail": "AI 暫時無法產生摘要（Codex 未啟用或呼叫失敗）"}, status_code=503)
        db.set_summary(conn, "tenders", tid, result)
        return {"summary": result, "cached": False}


@app.post("/api/tenders/{tid}/dissect")
def dissect_tender(tid: int, force: bool = False):
    with db.get_conn() as conn:
        row = search.get_one(conn, "tenders", tid)
        if not row:
            return JSONResponse({"detail": "查無此標案"}, status_code=404)
        if row.get("dissect") and not force:
            cached = row["dissect"]
            if isinstance(cached, str):
                try:
                    cached = json.loads(cached)
                except (ValueError, TypeError):
                    pass
            return {"dissect": cached, "cached": True}
        result = dissect_ai.dissect(row)
        if not result:
            return JSONResponse(
                {"detail": "AI 解構失敗；Codex 不可用或輸出非 JSON。"}, status_code=503
            )
        db.set_dissect(conn, tid, result)
        return {"dissect": result, "cached": False}


@app.post("/api/grants/{gid}/summarize")
def summarize_grant(gid: int, force: bool = False):
    with db.get_conn() as conn:
        row = search.get_one(conn, "grants", gid)
        if not row:
            return JSONResponse({"detail": "查無此補助案"}, status_code=404)
        if row.get("summary") and not force:
            return {"summary": row["summary"], "cached": True}
        result = summarize.summarize(summarize.grant_body(row), kind="補助案")
        if not result:
            return JSONResponse(
                {"detail": "AI 暫時無法產生摘要（Codex 未啟用或呼叫失敗）"}, status_code=503)
        db.set_summary(conn, "grants", gid, result)
        return {"summary": result, "cached": False}
