import json
import sqlite3

import pytest
from bs4 import BeautifulSoup

from app import db, search
from app.crawlers import grants, pcc_g0v
from app.main import _split_tags


def _init_db(monkeypatch, tmp_path):
    db_path = tmp_path / "round3.db"
    monkeypatch.setattr(db.settings, "DB_PATH", str(db_path))
    db.init_db()
    return db_path


def _source(conn):
    return db.ensure_source(conn, "round3", "https://example.test", "manual")


def test_split_tags_trims_empty_segments_and_preserves_order():
    assert _split_tags(" AI, ,cloud,,資安 ,AI ") == ["AI", "cloud", "資安", "AI"]
    assert _split_tags("") == []
    assert _split_tags(None) == []


def test_search_tenders_multiple_tags_require_all_tags(monkeypatch, tmp_path):
    _init_db(monkeypatch, tmp_path)
    with db.get_conn() as conn:
        source_id = _source(conn)
        rows = [
            ("J1", "AI cloud tender", ["AI", "cloud"]),
            ("J2", "AI only tender", ["AI"]),
            ("J3", "cloud only tender", ["cloud"]),
        ]
        for job_number, title, tags in rows:
            db.upsert_tender(
                conn,
                {
                    "source_id": source_id,
                    "unit_id": "U",
                    "job_number": job_number,
                    "filename": job_number,
                    "agency": "Agency",
                    "title": title,
                    "deadline": "2026-08-01",
                    "tags": json.dumps(tags, ensure_ascii=False),
                },
            )
        result = search.search_tenders(conn, tags=["AI", "cloud"])

    assert result["total"] == 1
    assert result["items"][0]["job_number"] == "J1"


def test_search_zero_page_size_returns_total_without_rows(monkeypatch, tmp_path):
    _init_db(monkeypatch, tmp_path)
    with db.get_conn() as conn:
        source_id = _source(conn)
        for idx in range(2):
            db.upsert_grant(
                conn,
                {
                    "source_id": source_id,
                    "agency": "Agency",
                    "title": f"Grant {idx}",
                    "target": "SME",
                    "apply_end": "2026-09-01",
                    "url": f"https://example.test/grant/{idx}",
                },
            )
        result = search.search_grants(conn, page_size=0)

    assert result["total"] == 2
    assert result["page_size"] == 0
    assert result["items"] == []


def test_pcc_detail_fields_fallback_prefers_record_with_deadline_when_filename_missing():
    detail_resp = {
        "records": [
            {"filename": "first", "detail": {"其他欄位": "x"}},
            {
                "filename": "deadline-record",
                "detail": {
                    "截止投標期限": "115/05/22 17:00",
                    "公告日": "115/05/01",
                    "預算金額": "1,000,000元",
                },
            },
        ]
    }

    fields = pcc_g0v._extract_detail_fields(detail_resp, "missing")

    assert fields["raw"]["filename"] == "deadline-record"
    assert fields["deadline"] == "2026-05-22"
    assert fields["deadline_time"] == "17:00"
    assert fields["publish_date_detail"] == "2026-05-01"
    assert fields["budget"] == "1,000,000元"


def test_pcc_tender_type_classifier_excludes_awards_and_accepts_tender_like_types():
    assert pcc_g0v._is_tender_type("公開招標公告") is True
    assert pcc_g0v._is_tender_type("公開取得報價單") is True
    assert pcc_g0v._is_tender_type("資格審查公告") is True
    assert pcc_g0v._is_tender_type("決標公告") is False
    assert pcc_g0v._is_tender_type("") is False


def test_default_html_parser_resolves_relative_url_and_handles_missing_link():
    source = grants.HtmlSource(
        name="html-test",
        list_url="https://example.test/list",
        item_selector=".item",
        title_selector=".title",
        link_selector="a.detail",
        base_url="https://example.test/base/",
    )
    soup = BeautifulSoup(
        """
        <div class="item"><span class="title">Grant A</span><a class="detail" href="../grant/a">open</a></div>
        <div class="item"><span class="title">No link</span></div>
        """,
        "lxml",
    )
    items = soup.select(".item")

    parsed = grants._default_parse(items[0], source)

    assert parsed == {"title": "Grant A", "url": "https://example.test/grant/a"}
    assert grants._default_parse(items[1], source) is None


def test_ingest_seed_missing_file_fails_before_db_write(monkeypatch, tmp_path):
    _init_db(monkeypatch, tmp_path)

    with pytest.raises(FileNotFoundError):
        grants.ingest_seed(str(tmp_path / "missing.json"))

    with db.get_conn() as conn:
        count = conn.execute("SELECT COUNT(*) AS n FROM crawl_runs").fetchone()["n"]

    assert count == 0


def test_get_one_rejects_unapproved_table_name_before_sql(monkeypatch, tmp_path):
    _init_db(monkeypatch, tmp_path)

    with db.get_conn() as conn:
        with pytest.raises(ValueError, match="table"):
            search.get_one(conn, "tenders WHERE 1=1 --", 1)


def test_get_one_invalid_table_is_refused_before_any_sql_runs(monkeypatch, tmp_path):
    # this used to surface as a sqlite3.Error, i.e. only after the statement had been
    # handed to SQLite with the caller's string already inside it
    _init_db(monkeypatch, tmp_path)

    with db.get_conn() as conn:
        with pytest.raises(ValueError, match="table"):
            search.get_one(conn, "not_a_real_table", 1)
