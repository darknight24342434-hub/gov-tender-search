import json

from fastapi.testclient import TestClient

from app import db, search
from app.auth import make_token


def _init_db(monkeypatch, tmp_path):
    db_path = tmp_path / "edge_paths.db"
    monkeypatch.setattr(db.settings, "DB_PATH", str(db_path))
    db.init_db()


def _source(conn):
    return db.ensure_source(conn, "edge-test", "https://example.test", "manual")


def test_search_tenders_keeps_malformed_json_as_text_and_hides_raw_json(monkeypatch, tmp_path):
    _init_db(monkeypatch, tmp_path)
    with db.get_conn() as conn:
        source_id = _source(conn)
        db.upsert_tender(
            conn,
            {
                "source_id": source_id,
                "unit_id": "U1",
                "job_number": "J1",
                "filename": "F1",
                "agency": "Agency",
                "title": "Malformed tags tender",
                "deadline": "2026-08-01",
                "tags": "[not-json",
                "summary": None,
                "raw_json": json.dumps({"internal": True}),
            },
        )
        result = search.search_tenders(conn, q="Malformed")

    assert result["total"] == 1
    assert result["items"][0]["tags"] == "[not-json"
    assert "raw_json" not in result["items"][0]


def test_get_one_parses_detail_json_fields_and_preserves_invalid_values(monkeypatch, tmp_path):
    _init_db(monkeypatch, tmp_path)
    with db.get_conn() as conn:
        source_id = _source(conn)
        db.upsert_grant(
            conn,
            {
                "source_id": source_id,
                "agency": "Grant Agency",
                "title": "Grant with raw detail",
                "target": "SME",
                "tags": json.dumps(["AI"]),
                "summary": None,
                "apply_end": "2026-09-01",
                "url": "https://example.test/grant/raw",
                "raw_json": json.dumps({"description": "source payload"}),
            },
        )
        row_id = conn.execute("SELECT id FROM grants").fetchone()["id"]
        conn.execute("UPDATE grants SET summary=? WHERE id=?", ("{bad-json", row_id))
        detail = search.get_one(conn, "grants", row_id)

    assert detail["tags"] == ["AI"]
    assert detail["raw_json"] == {"description": "source payload"}
    assert detail["summary"] == "{bad-json"


def test_search_pagination_boundaries_clamp_offset_but_echo_requested_page(monkeypatch, tmp_path):
    _init_db(monkeypatch, tmp_path)
    with db.get_conn() as conn:
        source_id = _source(conn)
        for idx in range(3):
            db.upsert_grant(
                conn,
                {
                    "source_id": source_id,
                    "agency": "Grant Agency",
                    "title": f"Grant {idx}",
                    "target": "SME",
                    "apply_end": f"2026-09-0{idx + 1}",
                    "url": f"https://example.test/grant/{idx}",
                },
            )
        zero_page = search.search_grants(conn, page=0, page_size=2)
        negative_page = search.search_grants(conn, page=-5, page_size=2)
        second_page = search.search_grants(conn, page=2, page_size=2)

    assert zero_page["page"] == 0
    assert negative_page["page"] == -5
    assert [item["title"] for item in zero_page["items"]] == [
        item["title"] for item in negative_page["items"]
    ]
    assert len(second_page["items"]) == 1


def test_awards_endpoint_summary_handles_nulls_and_ranks_winners(monkeypatch, tmp_path):
    _init_db(monkeypatch, tmp_path)
    with db.get_conn() as conn:
        rows = [
            ("U1", "J1", "F1", "Agency A", "AI system", "Winner B", 0.8, 1),
            ("U2", "J2", "F2", "Agency A", "AI system 2", "Winner A", 0.6, 3),
            ("U3", "J3", "F3", "Agency B", "Archive", "Winner B", None, None),
        ]
        for unit_id, job_number, filename, agency, title, winner, ratio, bidders in rows:
            db.upsert_award(
                conn,
                {
                    "unit_id": unit_id,
                    "job_number": job_number,
                    "filename": filename,
                    "agency": agency,
                    "title": title,
                    "winner": winner,
                    "ratio": ratio,
                    "bidders": bidders,
                    "award_date": "2026-07-01",
                    "raw_json": json.dumps({"hidden": True}),
                },
            )

    from app.config import settings
    from app.main import app

    with TestClient(app) as client:
        client.cookies.set(settings.COOKIE_NAME, make_token())
        response = client.get("/api/awards/search", params={"q": "AI"})

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    assert body["summary"]["median_ratio"] == 0.7
    assert body["summary"]["avg_bidders"] == 2.0
    assert body["summary"]["single_bidder_count"] == 1
    assert body["summary"]["top_winners"] == [
        {"winner": "Winner A", "count": 1},
        {"winner": "Winner B", "count": 1},
    ]
    assert "raw_json" not in body["items"][0]
