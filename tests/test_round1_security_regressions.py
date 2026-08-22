"""Both of these started life as strict xfails recording known weaknesses.
Both are now fixed, so they are ordinary assertions.

The session test originally asserted that a token signed with the documented default
SECRET_KEY must be rejected. That default no longer exists — config.py refuses to fall
back to a predictable key at all — so the test now covers the behaviour that replaced
it: a token signed with any other key is rejected, and the config fails closed.
"""
import importlib

import pytest
from itsdangerous import URLSafeTimedSerializer

from app import auth, db
from scripts import dispatch_enrich


def test_session_token_signed_with_another_key_is_rejected(monkeypatch):
    monkeypatch.setattr(
        auth, "_serializer", URLSafeTimedSerializer("the-real-secret", salt="gov-tender-auth")
    )

    forged = URLSafeTimedSerializer("not-the-real-secret", salt="gov-tender-auth").dumps(
        {"ok": True}
    )

    assert auth.valid_token(forged) is False


def test_config_fails_closed_when_password_gate_is_on_without_a_secret_key(monkeypatch):
    monkeypatch.setenv("APP_PASSWORD", "something")
    monkeypatch.delenv("SECRET_KEY", raising=False)

    from app import config

    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        importlib.reload(config)

    # leave the module in a usable state for anything that imports it afterwards
    monkeypatch.setenv("SECRET_KEY", "test-secret-for-reload")
    importlib.reload(config)


def test_dispatch_collect_jobs_rejects_unapproved_kind_before_sql(monkeypatch, tmp_path):
    db_path = tmp_path / "dispatch.db"
    monkeypatch.setattr(db.settings, "DB_PATH", str(db_path))
    db.init_db()

    with pytest.raises(ValueError, match="kind"):
        dispatch_enrich.collect_jobs(["tenders WHERE 1=1 --"], ["summary"])
