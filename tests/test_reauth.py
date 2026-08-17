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
