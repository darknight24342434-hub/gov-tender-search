import json
from pathlib import Path

from fastapi.testclient import TestClient

from app import db, search
from app.auth import make_token
from app.roc_date import normalize_iso, roc_to_iso, yyyymmdd_to_iso


def init_temp_db(monkeypatch, tmp_path: Path) -> Path:
    db_path = tmp_path / "tenders_test.db"
    monkeypatch.setattr(db.settings, "DB_PATH", str(db_path))
    db.init_db()
    return db_path


def seed_records(conn):
    source_id = db.ensure_source(conn, "unit-test", "https://example.test", "manual")
    db.upsert_tender(
        conn,
        {
            "source_id": source_id,
            "unit_id": "A01",
            "job_number": "JOB-001",
            "filename": "f1",
            "agency": "台北市政府",
            "title": "AI 資料平台建置案",
            "type": "勞務採購",
            "budget": "1000000",
            "publish_date": "2026-06-01",
            "deadline": "2026-08-10",
            "deadline_time": "17:00",
            "url": "https://example.test/tender/1",
            "tags": json.dumps(["AI", "資料"], ensure_ascii=False),
            "raw_json": json.dumps({"secret": "kept out of list"}, ensure_ascii=False),
        },
    )
    db.upsert_tender(
        conn,
        {
            "source_id": source_id,
            "unit_id": "A02",
            "job_number": "JOB-002",
            "filename": "f2",
            "agency": "文化部",
            "title": "展演空間維護",
            "type": "財物採購",
            "publish_date": "2026-06-02",
            "deadline": "2026-07-01",
            "url": "https://example.test/tender/2",
            "tags": json.dumps(["文化"], ensure_ascii=False),
            "raw_json": json.dumps({"raw": True}, ensure_ascii=False),
        },
    )
    db.upsert_grant(
        conn,
        {
            "source_id": source_id,
            "agency": "經濟部",
            "title": "中小企業 AI 升級補助",
            "target": "中小企業",
            "tags": json.dumps(["AI", "補助"], ensure_ascii=False),
            "apply_start": "2026-06-01",
            "apply_end": "2026-09-30",
            "url": "https://example.test/grant/1",
            "raw_json": json.dumps({"description": "導入 AI 工具"}, ensure_ascii=False),
        },
    )
    conn.commit()


def test_roc_date_normalization_accepts_roc_iso_and_yyyymmdd():
    assert roc_to_iso("115/05/22 17:00") == ("2026-05-22", "17:00")
    assert roc_to_iso("2026-05-22") == ("2026-05-22", None)
    assert roc_to_iso("115/02/30") == (None, None)
    assert yyyymmdd_to_iso(20260617) == "2026-06-17"
    assert yyyymmdd_to_iso("20260230") is None
    assert normalize_iso("115/05/22 17:00") == "2026-05-22"
    assert normalize_iso("20260617") == "2026-06-17"


def test_upsert_tender_updates_existing_unique_record(monkeypatch, tmp_path):
    init_temp_db(monkeypatch, tmp_path)
    with db.get_conn() as conn:
        source_id = db.ensure_source(conn, "unit-test", "https://example.test", "manual")
        row = {
            "source_id": source_id,
            "unit_id": "A01",
            "job_number": "JOB-001",
            "filename": "same",
            "agency": "A",
            "title": "原始標題",
        }
        assert db.upsert_tender(conn, row) == "inserted"
        row["title"] = "更新後標題"
        assert db.upsert_tender(conn, row) == "updated"
        rows = conn.execute("SELECT title FROM tenders").fetchall()
    assert [r["title"] for r in rows] == ["更新後標題"]


def test_search_tenders_filters_tags_type_deadline_and_hides_raw_json(monkeypatch, tmp_path):
    init_temp_db(monkeypatch, tmp_path)
    with db.get_conn() as conn:
        seed_records(conn)
        result = search.search_tenders(
            conn,
            q="資料平台",
            tags=["AI"],
            type_="勞務",
            deadline_from="2026-08-01",
            deadline_to="2026-08-31",
        )
    assert result["total"] == 1
    item = result["items"][0]
    assert item["job_number"] == "JOB-001"
    assert item["tags"] == ["AI", "資料"]
    assert "raw_json" not in item


def test_search_grants_filters_target_and_tag(monkeypatch, tmp_path):
    init_temp_db(monkeypatch, tmp_path)
    with db.get_conn() as conn:
        seed_records(conn)
        result = search.search_grants(conn, q="AI", tags=["補助"], target="中小")
    assert result["total"] == 1
    assert result["items"][0]["agency"] == "經濟部"
    assert result["items"][0]["tags"] == ["AI", "補助"]


def test_fastapi_search_endpoint_requires_auth_then_returns_seeded_tender(monkeypatch, tmp_path):
    init_temp_db(monkeypatch, tmp_path)
    with db.get_conn() as conn:
        seed_records(conn)

    from app.main import app
    from app.config import settings

    with TestClient(app) as client:
        unauthorized = client.get("/api/search/tenders")
        assert unauthorized.status_code == 401

        client.cookies.set(settings.COOKIE_NAME, make_token())
        response = client.get("/api/search/tenders", params={"q": "資料平台", "tags": "AI"})

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["title"] == "AI 資料平台建置案"
