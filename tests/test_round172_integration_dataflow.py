from contextlib import contextmanager

import pytest

from app import main


@pytest.mark.xfail(
    reason=(
        "summarize_tender currently invokes the external AI summarizer while "
        "the SQLite connection context is still open"
    ),
    strict=True,
)
def test_summarize_tender_does_not_hold_db_context_during_ai_call(monkeypatch):
    state = {"db_context_open": False, "ai_saw_open_context": None}
    row = {
        "id": 1,
        "title": "Tender",
        "agency": "Agency",
        "type": "service",
        "summary": None,
    }

    @contextmanager
    def fake_get_conn():
        state["db_context_open"] = True
        try:
            yield object()
        finally:
            state["db_context_open"] = False

    def fake_summarize(body, kind):
        state["ai_saw_open_context"] = state["db_context_open"]
        return {
            "who": "SME",
            "threshold": "none",
            "deadline": "none",
            "next_step": "review",
        }

    monkeypatch.setattr(main.db, "get_conn", fake_get_conn)
    monkeypatch.setattr(main.search, "get_one", lambda conn, table, row_id: row)
    monkeypatch.setattr(main.summarize, "summarize", fake_summarize)
    monkeypatch.setattr(main.db, "set_summary", lambda conn, table, row_id, summary: None)

    response = main.summarize_tender(1)

    assert response["cached"] is False
    assert state["ai_saw_open_context"] is False
