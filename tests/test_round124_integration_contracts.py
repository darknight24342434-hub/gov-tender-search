import json
import sys
from pathlib import Path

import pytest

from app import db

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from scripts.dispatch_enrich import EnrichJob, _write_result


def _init_db(monkeypatch, tmp_path):
    db_path = tmp_path / "round124.db"
    monkeypatch.setattr(db.settings, "DB_PATH", str(db_path))
    db.init_db()


@pytest.mark.xfail(
    reason="upsert_tender uses nullable identity columns, so SQLite UNIQUE and '=' lookup do not coalesce NULL keys",
    strict=True,
)
def test_upsert_tender_treats_missing_identity_as_same_record(monkeypatch, tmp_path):
    _init_db(monkeypatch, tmp_path)
    with db.get_conn() as conn:
        source_id = db.ensure_source(conn, "round124", "https://example.test", "manual")
        row = {
            "source_id": source_id,
            "unit_id": "U1",
            "job_number": "J1",
            "filename": None,
            "agency": "Agency",
            "title": "Tender with missing filename",
        }

        first = db.upsert_tender(conn, row)
        row["title"] = "Updated tender"
        second = db.upsert_tender(conn, row)
        rows = conn.execute("SELECT title FROM tenders").fetchall()

    assert (first, second) == ("inserted", "updated")
    assert [r["title"] for r in rows] == ["Updated tender"]


@pytest.mark.xfail(
    reason="dispatch enrichment writes results unconditionally after worker completion and can overwrite a value written by another worker",
    strict=True,
)
def test_dispatch_write_result_does_not_overwrite_existing_summary(monkeypatch, tmp_path):
    _init_db(monkeypatch, tmp_path)
    with db.get_conn() as conn:
        source_id = db.ensure_source(conn, "round124", "https://example.test", "manual")
        db.upsert_grant(
            conn,
            {
                "source_id": source_id,
                "agency": "Grant Agency",
                "title": "Grant",
                "target": "SME",
                "url": "https://example.test/grant",
                "summary": json.dumps({"who": "fresh"}, ensure_ascii=False),
            },
        )
        row_id = conn.execute("SELECT id FROM grants").fetchone()["id"]

    stale_job = EnrichJob(
        kind="grants",
        row_id=row_id,
        field="summary",
        title="Grant",
        prompt="stale prompt",
    )
    _write_result(stale_job, {"who": "stale", "threshold": "", "deadline": "", "next_step": ""})

    with db.get_conn() as conn:
        saved = json.loads(conn.execute("SELECT summary FROM grants WHERE id=?", (row_id,)).fetchone()["summary"])

    assert saved["who"] == "fresh"
