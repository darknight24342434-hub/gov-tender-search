import json
import sqlite3

import pytest

from app import db
from app.ai import codex_client, summarize, tagging
from app.crawlers import pcc_awards


def _init_db(monkeypatch, tmp_path):
    db_path = tmp_path / "round171.db"
    monkeypatch.setattr(db.settings, "DB_PATH", str(db_path))
    db.init_db()
    return db_path


def test_codex_extract_json_prefers_fenced_object_and_ignores_noise():
    text = """
    human preface
    ```json
    {"who": "SME", "threshold": "under 5m"}
    ```
    trailing explanation
    """

    assert codex_client.extract_json(text) == {"who": "SME", "threshold": "under 5m"}


def test_codex_extract_json_salvages_last_balanced_object_from_text():
    text = 'not json {"outer": {"inner": 1}, "items": [1, 2]} trailing } noise'

    assert codex_client.extract_json(text) == {
        "outer": {"inner": 1},
        "items": [1, 2],
    }


def test_codex_extract_json_returns_none_for_missing_or_broken_object():
    assert codex_client.extract_json(None) is None
    assert codex_client.extract_json("") is None
    assert codex_client.extract_json("prefix {'not': 'json'} suffix") is None


@pytest.mark.xfail(
    reason="codex_client.extract_json returns any JSON value; callers expect an object/dict contract",
    strict=True,
)
def test_codex_extract_json_rejects_non_object_json_contract():
    assert codex_client.extract_json('["not", "an", "object"]') is None


def test_tagging_normalize_result_dedupes_filters_and_caps_to_five():
    allowed = list(db.DEFAULT_TAGS)[:6]
    result = {
        "tags": [
            f" {allowed[0]} ",
            "not-in-vocab",
            allowed[1],
            allowed[0],
            allowed[2],
            allowed[3],
            allowed[4],
            allowed[5],
        ]
    }

    assert tagging.normalize_result(result) == allowed[:5]
    assert tagging.normalize_result({"tags": "not-a-list"}) == []
    assert tagging.normalize_result(None) == []


def test_summarize_normalize_result_strips_values_and_fills_missing_keys():
    normalized = summarize.normalize_result({"who": "  company  ", "threshold": ""})

    assert normalized["who"] == "company"
    assert set(normalized) == {"who", "threshold", "deadline", "next_step"}
    assert normalized["threshold"]
    assert normalized["deadline"]
    assert normalized["next_step"]
    assert summarize.normalize_result(None) is None


def test_award_money_and_ratio_helpers_handle_empty_text_and_zero_budget():
    assert pcc_awards._money_to_int("NT$ 1,234,567") == 1_234_567
    assert pcc_awards._money_to_int("no digits") is None
    assert pcc_awards._int_from_text("共 12 家投標") == 12
    assert pcc_awards._int_from_text("none") is None
    assert pcc_awards._ratio(100, 0) is None
    assert pcc_awards._ratio(0, 100) is None
    assert pcc_awards._ratio(333, 1000) == 0.333


def test_set_tags_rejects_unapproved_table_name_before_sql(monkeypatch, tmp_path):
    _init_db(monkeypatch, tmp_path)

    with db.get_conn() as conn:
        with pytest.raises(ValueError, match="table"):
            db.set_tags(conn, "not_a_real_table", 1, ["AI"])


def test_set_summary_rejects_unapproved_table_name_before_sql(monkeypatch, tmp_path):
    _init_db(monkeypatch, tmp_path)

    with db.get_conn() as conn:
        with pytest.raises(ValueError, match="table"):
            db.set_summary(conn, "not_a_real_table", 1, {"who": "x"})


def test_set_tags_and_summary_round_trip_valid_table(monkeypatch, tmp_path):
    _init_db(monkeypatch, tmp_path)

    with db.get_conn() as conn:
        source_id = db.ensure_source(conn, "round171", "https://example.test", "manual")
        db.upsert_grant(
            conn,
            {
                "source_id": source_id,
                "agency": "Agency",
                "title": "Grant",
                "target": "SME",
                "url": "https://example.test/grant",
            },
        )
        row_id = conn.execute("SELECT id FROM grants").fetchone()["id"]
        db.set_tags(conn, "grants", row_id, ["AI", "cloud"])
        db.set_summary(conn, "grants", row_id, {"who": "SME"})
        saved = conn.execute("SELECT tags, summary FROM grants WHERE id=?", (row_id,)).fetchone()

    assert json.loads(saved["tags"]) == ["AI", "cloud"]
    assert json.loads(saved["summary"]) == {"who": "SME"}


@pytest.mark.xfail(
    reason="upsert_award uses nullable identity columns, so SQLite UNIQUE and '=' lookup do not coalesce NULL keys",
    strict=True,
)
def test_upsert_award_treats_missing_filename_as_same_record(monkeypatch, tmp_path):
    _init_db(monkeypatch, tmp_path)

    with db.get_conn() as conn:
        row = {
            "unit_id": "U1",
            "job_number": "J1",
            "filename": None,
            "agency": "Agency",
            "title": "Initial award",
        }
        first = db.upsert_award(conn, row)
        row["title"] = "Updated award"
        second = db.upsert_award(conn, row)
        titles = [r["title"] for r in conn.execute("SELECT title FROM awards").fetchall()]

    assert (first, second) == ("inserted", "updated")
    assert titles == ["Updated award"]


def test_invalid_table_name_is_refused_before_any_sql_runs(monkeypatch, tmp_path):
    # this used to surface as a sqlite3.Error, i.e. only after the statement had been
    # handed to SQLite with the caller's string already inside it
    _init_db(monkeypatch, tmp_path)

    with db.get_conn() as conn:
        with pytest.raises(ValueError, match="table"):
            db.set_tags(conn, "not_a_real_table", 1, ["AI"])
