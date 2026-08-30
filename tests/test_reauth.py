import base64
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import pytest
from unittest.mock import MagicMock, patch

import reauth


def test_extract_code_from_bare_code():
    assert reauth.extract_code("AQA_abc123") == "AQA_abc123"


def test_extract_code_strips_ubi_tail():
    """The callback appends &ubi=..., which is not part of the code"""
    assert reauth.extract_code("AQA_abc123&ubi=CAIQ%3D%3D") == "AQA_abc123"


def test_extract_code_from_full_redirect_url():
    url = ("https://jbernet.com/spotify/callback?code=AQA_abc123"
           "&ubi=CAIQwK7xzIA0GiRi%3D%3D")
    assert reauth.extract_code(url) == "AQA_abc123"


def test_extract_code_rejects_empty():
    with pytest.raises(ValueError):
        reauth.extract_code("   ")


def test_authorize_url_forces_account_chooser():
    url = reauth.build_authorize_url("client123")
    assert "show_dialog=true" in url
    assert "client_id=client123" in url
    # Every scope the player relies on must be requested; they are fixed at
    # authorization time and cannot be widened later without redoing this.
    for scope in reauth.SCOPES:
        assert scope in url


def test_validate_rejects_wrong_account():
    """The trap: a wrong-account token authenticates fine but sees no device"""
    me = MagicMock(status_code=200)
    me.json.return_value = {"id": "someone_else"}
    devices = MagicMock(status_code=200)
    devices.json.return_value = {"devices": [{"id": "dev1", "name": "toem2",
                                              "type": "Speaker"}]}

    with patch("reauth.requests.get", side_effect=[me, devices]):
        problems = reauth.validate("tok", "expected_user", "dev1")

    assert any("wrong account" in p for p in problems)


def test_validate_rejects_missing_device():
    me = MagicMock(status_code=200)
    me.json.return_value = {"id": "expected_user"}
    devices = MagicMock(status_code=200)
    devices.json.return_value = {"devices": []}

    with patch("reauth.requests.get", side_effect=[me, devices]):
        problems = reauth.validate("tok", "expected_user", "dev1")

    assert any("not in the device list" in p for p in problems)


def test_validate_passes_when_account_and_device_match():
    me = MagicMock(status_code=200)
    me.json.return_value = {"id": "expected_user"}
    devices = MagicMock(status_code=200)
    devices.json.return_value = {"devices": [{"id": "dev1", "name": "toem2",
                                              "type": "Speaker"}]}

    with patch("reauth.requests.get", side_effect=[me, devices]):
        assert reauth.validate("tok", "expected_user", "dev1") == []


def test_validate_skips_account_check_when_unknown():
    """Unknown spotifyd account should not block, only skip that check"""
    me = MagicMock(status_code=200)
    me.json.return_value = {"id": "whoever"}
    devices = MagicMock(status_code=200)
    devices.json.return_value = {"devices": [{"id": "dev1", "name": "x",
                                              "type": "Speaker"}]}

    with patch("reauth.requests.get", side_effect=[me, devices]):
        assert reauth.validate("tok", None, "dev1") == []


def test_read_env_parses_and_ignores_comments():
    text = "# comment\nA=1\n\nB=two=three\n"
    with patch("reauth.run", return_value=text):
        env = reauth.read_env(None, ".env")
    assert env == {"A": "1", "B": "two=three"}


def test_exchange_raises_with_body_on_failure():
    response = MagicMock(status_code=400, text='{"error":"invalid_grant"}')
    with patch("reauth.requests.post", return_value=response):
        with pytest.raises(RuntimeError, match="invalid_grant"):
            reauth.exchange("code", "creds")


def _fake_run(files=None, journal=""):
    """Stand in for reauth.run, answering `cat <path>` and the journal read

    Paths are matched by their tail, so tests name "oauth" rather than the
    whole ~/.cache/spotifyd/... string.
    """
    files = files or {}

    def run(host, command):
        if command.startswith("journalctl"):
            return journal
        for fragment, body in files.items():
            if fragment in command:
                return body
        raise RuntimeError("no such file")

    return run


def test_spotifyd_account_prefers_the_journal_over_the_blobs():
    """Who is logged in now beats what the next start would use

    A Connect takeover switches the running session before any file changes,
    so the journal is the only source that is right during one.
    """
    run = _fake_run(files={"oauth/credentials.json": '{"username": "stale"}'},
                    journal="Authenticated as 'first' !\n"
                            "Authenticated as 'current' !\n")
    with patch("reauth.run", side_effect=run):
        assert reauth.spotifyd_account(None) == ("current",
                                                 "journal (logged in now)")


def test_spotifyd_account_falls_back_in_spotifyd_precedence_order():
    """oauth wins over zeroconf, which wins over the flat 0.3.x file"""
    run = _fake_run(files={
        "oauth/credentials.json": '{"username": "from_oauth"}',
        "zeroconf/credentials.json": '{"username": "from_zeroconf"}',
        "spotifyd/credentials.json": '{"username": "from_flat"}',
    })
    with patch("reauth.run", side_effect=run):
        assert reauth.spotifyd_account(None) == ("from_oauth", "oauth blob")


def test_spotifyd_account_skips_blobs_that_are_absent():
    run = _fake_run(files={"zeroconf/credentials.json":
                           '{"username": "from_zeroconf"}'})
    with patch("reauth.run", side_effect=run):
        assert reauth.spotifyd_account(None) == ("from_zeroconf",
                                                 "zeroconf blob")


def test_spotifyd_account_honours_an_explicit_path():
    """--spotifyd-credentials overrides journal and precedence alike"""
    run = _fake_run(files={"custom.json": '{"username": "explicit"}'},
                    journal="Authenticated as 'ignored' !\n")
    with patch("reauth.run", side_effect=run):
        assert reauth.spotifyd_account(None, "custom.json") == (
            "explicit", "custom.json")


def test_spotifyd_account_is_unknown_when_nothing_is_readable():
    with patch("reauth.run", side_effect=_fake_run()):
        assert reauth.spotifyd_account(None) == (None, None)


def test_disagreeing_blobs_names_a_blob_holding_another_account():
    """The running session can be right while the next start is not"""
    run = _fake_run(files={
        "oauth/credentials.json": '{"username": "ours"}',
        "zeroconf/credentials.json": '{"username": "theirs"}',
    })
    with patch("reauth.run", side_effect=run):
        assert reauth.disagreeing_blobs(None, "ours") == [("zeroconf",
                                                           "theirs")]


def test_disagreeing_blobs_is_empty_when_every_blob_agrees():
    run = _fake_run(files={
        "oauth/credentials.json": '{"username": "ours"}',
        "zeroconf/credentials.json": '{"username": "ours"}',
    })
    with patch("reauth.run", side_effect=run):
        assert reauth.disagreeing_blobs(None, "ours") == []
