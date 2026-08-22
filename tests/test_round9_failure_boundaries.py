import json

from fastapi.testclient import TestClient

from app import db
from app.auth import make_token
from app.crawlers import pcc_awards


def _init_temp_db(monkeypatch, tmp_path):
    db_path = tmp_path / "round9.db"
    monkeypatch.setattr(db.settings, "DB_PATH", str(db_path))
    db.init_db()
    return db_path


def _auth_client():
    from app.config import settings
    from app.main import app

    client = TestClient(app)
    client.cookies.set(settings.COOKIE_NAME, make_token())
    return client


def test_get_conn_rolls_back_source_insert_on_exception(monkeypatch, tmp_path):
    _init_temp_db(monkeypatch, tmp_path)

    try:
        with db.get_conn() as conn:
            db.ensure_source(conn, "will-rollback", "https://example.test", "manual")
            raise RuntimeError("force rollback")
    except RuntimeError:
        pass

    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM sources WHERE name=?",
            ("will-rollback",),
        ).fetchone()

    assert row["n"] == 0


def test_missing_detail_api_returns_404_without_leaking_traceback(monkeypatch, tmp_path):
    _init_temp_db(monkeypatch, tmp_path)

    with _auth_client() as client:
        tender_response = client.get("/api/tenders/999")
        grant_response = client.get("/api/grants/999")

    assert tender_response.status_code == 404
    assert grant_response.status_code == 404
    assert set(tender_response.json()) == {"detail"}
    assert set(grant_response.json()) == {"detail"}


def test_summarize_tender_cached_response_and_forced_ai_failure(monkeypatch, tmp_path):
    _init_temp_db(monkeypatch, tmp_path)
    cached = {
        "who": "cached who",
        "threshold": "cached threshold",
        "deadline": "cached deadline",
        "next_step": "cached next step",
    }
    with db.get_conn() as conn:
        source_id = db.ensure_source(conn, "summary-test", "https://example.test", "manual")
        db.upsert_tender(
            conn,
            {
                "source_id": source_id,
                "unit_id": "U1",
                "job_number": "J1",
                "filename": "F1",
                "agency": "Agency",
                "title": "Cached tender",
                "summary": json.dumps(cached),
            },
        )
        tender_id = conn.execute("SELECT id FROM tenders").fetchone()["id"]
        db.set_summary(conn, "tenders", tender_id, cached)

    from app.main import summarize

    calls = []

    def fail_summarize(*args, **kwargs):
        calls.append((args, kwargs))
        return None

    monkeypatch.setattr(summarize, "summarize", fail_summarize)

    with _auth_client() as client:
        cached_response = client.post(f"/api/tenders/{tender_id}/summarize")
        forced_response = client.post(
            f"/api/tenders/{tender_id}/summarize",
            params={"force": "true"},
        )

    assert cached_response.status_code == 200
    assert cached_response.json() == {"summary": cached, "cached": True}
    assert forced_response.status_code == 503
    assert len(calls) == 1

    with db.get_conn() as conn:
        stored = conn.execute(
            "SELECT summary FROM tenders WHERE id=?",
            (tender_id,),
        ).fetchone()["summary"]

    assert json.loads(stored) == cached


def test_award_helpers_extract_nested_money_ratio_bidders_and_absolute_url():
    source_record = {
        "unit_id": "U1",
        "job_number": "J1",
        "filename": "F1",
        "unit_name": "Agency",
        "date": 20260701,
        "url": "/tender/path",
        "brief": {
            "title": "Award title",
            "companies": {"names": [" Winner A ", "", "Winner B"]},
        },
    }
    detail_record = {
        "detail": {
            "outer": {
                pcc_awards.BUDGET_KEY: "1,000,000元",
                "決標金額": "850,000元",
                "投標廠商家數": "3家",
            }
        }
    }

    row = pcc_awards._row_from_record(source_record, detail_record)

    assert row["winner"] == "Winner A、Winner B"
    assert row["budget"] == 1_000_000
    assert row["award_amount"] == 850_000
    assert row["ratio"] == 0.85
    assert row["bidders"] == 3
    assert row["award_date"] == "2026-07-01"
    assert row["url"].endswith("/tender/path")
    assert set(json.loads(row["raw_json"])) == {"search", "detail"}


def test_award_detail_selection_falls_back_to_first_record_when_filename_missing():
    detail_resp = {
        "records": [
            {"filename": "first", "detail": {"value": 1}},
            {"filename": "second", "detail": {"value": 2}},
        ]
    }

    assert pcc_awards._find_record_detail(detail_resp, "second")["filename"] == "second"
    assert pcc_awards._find_record_detail(detail_resp, "missing")["filename"] == "first"
    assert pcc_awards._find_record_detail({}, "missing") == {}
