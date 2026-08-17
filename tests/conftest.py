import os
import sys
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))


@pytest.fixture(autouse=True)
def spotify_env(monkeypatch):
    """Give every test valid Spotify credentials and a clean auth manager

    SpotifyPlayer.__init__ builds the auth manager eagerly, so any test that
    constructs a player needs these set even when it is not testing auth.
    Tests that check missing-variable behaviour delenv them again themselves.

    The module-level _auth_manager singleton is replaced with a stub, so that
    building a player never triggers a real token request. Without this, any
    test that reaches _get_headers() calls accounts.spotify.com for real with
    these fake credentials, then retries three times with 3s sleeps - which is
    where most of the suite's runtime used to go.

    Tests that exercise SpotifyAuthManager itself construct it directly and are
    unaffected by the singleton.
    """
    # A real .env leaking in (some modules call load_dotenv) would otherwise
    # decide which branch utils.shutdown() takes, and the os._exit() branch
    # terminates the test runner silently with exit code 0.
    monkeypatch.delenv("DEVELOPMENT", raising=False)

    monkeypatch.setenv("SPOTIFY_USERCREDS", "test_creds")
    monkeypatch.setenv("SPOTIFY_REFRESH_TOKEN", "test_token")
    monkeypatch.setenv("SPOTIFY_DEVICE_ID", "test_device")

    import spotify
    stub = MagicMock()
    stub.get_token.return_value = "test_access_token"
    spotify._auth_manager = stub
    yield
    spotify._auth_manager = None
