"""These two started life as strict xfails recording known weaknesses.
Both weaknesses are now fixed, so they are ordinary assertions.
"""
from pathlib import Path

from app import fleet


def test_settings_do_not_default_to_predictable_session_secret():
    config_text = Path("app/config.py").read_text(encoding="utf-8")

    assert "dev-insecure-change-me" not in config_text


def test_codex_worker_commands_keep_sandbox_and_approval_boundaries():
    assert "--dangerously-bypass-approvals-and-sandbox" not in fleet.CODEX_ARGS


def test_default_workers_carry_no_baked_in_machine_details():
    source = Path("app/fleet.py").read_text(encoding="utf-8")

    # no usernames, no LAN/Tailscale addresses, no private-key paths
    assert "192.168." not in source
    assert "100.120." not in source
    assert "id_ed25519" not in source
