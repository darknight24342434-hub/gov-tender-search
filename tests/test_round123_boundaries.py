import json

import pytest

from app import db, httpclient, search
from app.auth import make_token, valid_token, verify_password
from app.crawlers import grants, pcc_awards, pcc_g0v


def _init_db(monkeypatch, tmp_path):
    db_path = tmp_path / "round123.db"
    monkeypatch.setattr(db.settings, "DB_PATH", str(db_path))
    db.init_db()
    return db_path


def test_auth_rejects_empty_password_and_tampered_tokens(monkeypatch):
    monkeypatch.setattr("app.auth.settings.APP_PASSWORD", "")
    assert verify_password("anything") is False
    assert verify_password("") is False
    assert valid_token("") is False
    assert valid_token("not-a-valid-token") is False

    monkeypatch.setattr("app.auth.settings.APP_PASSWORD", "secret")
    assert verify_password(None) is False
    assert verify_password("wrong") is False
    assert verify_password("secret") is True

    token = make_token()
    assert valid_token(token) is True
    assert valid_token(token + "x") is False


def test_make_client_sets_expected_headers_and_referer(monkeypatch):
    sentinel_verify = object()
    monkeypatch.setattr(httpclient, "_ssl_context", lambda: sentinel_verify)
    monkeypatch.setattr(httpclient.settings, "HTTP_USER_AGENT", "round123-agent")

    client = httpclient.make_client(
        referer="https://example.test/from",
        accept="text/html",
    )
    try:
        assert client.headers["User-Agent"] == "round123-agent"
        assert client.headers["Accept"] == "text/html"
        assert client.headers["Accept-Language"] == "zh-TW,zh;q=0.9"
        assert client.headers["Referer"] == "https://example.test/from"
        assert client.timeout.connect == 30.0
        assert client.follow_redirects is True
    finally:
        client.close()


class _FakeResponse:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


class _FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, params):
        self.calls.append((url, params))
        return self.responses.pop(0)


def test_award_detail_fetch_retries_429_then_returns_json(monkeypatch):
    sleeps = []
    monkeypatch.setattr(pcc_awards, "DETAIL_429_BASE_DELAY", 0.5)
    monkeypatch.setattr(pcc_awards.time, "sleep", lambda seconds: sleeps.append(seconds))
    client = _FakeClient(
        [
            _FakeResponse(429),
            _FakeResponse(429),
            _FakeResponse(200, {"records": [{"filename": "ok"}]}),
        ]
    )

    result = pcc_awards.fetch_detail(client, "U1", "J1")

    assert result == {"records": [{"filename": "ok"}]}
    assert len(client.calls) == 3
    assert sleeps == [0.5, 1.0]


def test_award_detail_fetch_raises_after_retry_budget(monkeypatch):
    monkeypatch.setattr(pcc_awards, "DETAIL_429_BASE_DELAY", 0)
    monkeypatch.setattr(pcc_awards.time, "sleep", lambda seconds: None)
    client = _FakeClient([_FakeResponse(429) for _ in range(4)])

    with pytest.raises(RuntimeError, match="HTTP 429"):
        pcc_awards.fetch_detail(client, "U1", "J1")

    assert len(client.calls) == 4


def test_ingest_seed_skips_missing_url_and_normalizes_dates(monkeypatch, tmp_path):
    _init_db(monkeypatch, tmp_path)
    seed_path = tmp_path / "seed.json"
    seed_path.write_text(
        json.dumps(
            [
                {
                    "agency": "A",
                    "title": "No URL",
                    "target": "SME",
                    "url": "",
                },
                {
                    "agency": "A",
                    "title": "Grant",
                    "target": "SME",
                    "tags": ["AI"],
                    "apply_start": "115/05/01",
                    "apply_end": "20260630",
                    "url": "https://example.test/grant",
                    "description": "kept in raw_json",
                },
            ]
        ),
        encoding="utf-8",
    )

    stats = grants.ingest_seed(str(seed_path))

    assert stats == {"inserted": 1, "updated": 0, "skipped": 1}
    with db.get_conn() as conn:
        rows = conn.execute("SELECT * FROM grants").fetchall()
    assert len(rows) == 1
    row = dict(rows[0])
    assert row["apply_start"] == "2026-05-01"
    assert row["apply_end"] == "2026-06-30"
    assert json.loads(row["tags"]) == ["AI"]
    assert json.loads(row["raw_json"])["description"] == "kept in raw_json"


def test_extract_detail_fields_handles_empty_and_filename_fallbacks():
    assert pcc_g0v._extract_detail_fields({}, "missing") == {}

    detail = {
        "records": [
            {"filename": "first", "detail": {"unused": "1"}},
            {"filename": "target", "detail": {"unused": "2"}},
        ]
    }

    result = pcc_g0v._extract_detail_fields(detail, "target")

    assert result["raw"]["filename"] == "target"
    assert result["deadline"] is None
    assert result["deadline_time"] is None
    assert result["publish_date_detail"] is None
    assert result["budget"] is None


def test_search_negative_page_size_currently_disables_limit(monkeypatch, tmp_path):
    _init_db(monkeypatch, tmp_path)
    with db.get_conn() as conn:
        source_id = db.ensure_source(conn, "round123", "https://example.test", "manual")
        for idx in range(3):
            db.upsert_tender(
                conn,
                {
                    "source_id": source_id,
                    "unit_id": "U",
                    "job_number": f"J{idx}",
                    "filename": f"F{idx}",
                    "agency": "Agency",
                    "title": f"Tender {idx}",
                    "deadline": "2026-08-01",
                },
            )
        result = search.search_tenders(conn, page=1, page_size=-1)

    assert result["page_size"] == -1
    assert result["total"] == 3
    assert len(result["items"]) == 3
