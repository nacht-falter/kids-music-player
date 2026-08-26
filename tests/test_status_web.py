import datetime
import json
import sqlite3
import urllib.error
import urllib.request
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import status_web


@pytest.fixture(autouse=True)
def no_shell(monkeypatch):
    """Never run real shell commands from a test

    status_web asks the system about mpd, spotifyd, wifi and the journal.
    Left unpatched those either answer about the development machine or hang
    waiting for a tool that is not installed.
    """
    monkeypatch.setattr(status_web, "sh", lambda command, timeout=4: "")


@pytest.fixture
def app(tmp_path):
    """A stand-in for RFIDMusicPlayer with the attributes the page reads"""
    database_url = str(tmp_path / "toem.db")
    db = sqlite3.connect(database_url)
    db.executescript("""
        CREATE TABLE music (rfid TEXT PRIMARY KEY, source TEXT, playback_state TEXT,
                            location TEXT, title TEXT, last_modified TIMESTAMP);
        CREATE TABLE last_played (id INTEGER PRIMARY KEY, last_played_rfid TEXT);
        CREATE TABLE sync_meta (id INTEGER PRIMARY KEY, last_sync TIMESTAMP);
    """)
    db.execute("INSERT INTO music VALUES ('0001', 'spotify', NULL, "
               "'spotify:album:x', 'Bibi Blocksberg', NULL)")
    db.execute("INSERT INTO music VALUES ('0002', 'local', NULL, 'Tanzmaus', "
               "'Tanzmaus', NULL)")
    db.commit()
    db.close()

    import threading
    return SimpleNamespace(
        database_url=database_url,
        log_path=str(tmp_path / "toem.log"),
        activity_lock=threading.Lock(),
        last_activity=0.0,
        idle_time=1800,
        rfid_reader=object(),
        button_handler=object(),
        player=None,
        get_player=lambda: None,
        reset_last_activity=lambda: None,
    )


def value_of(rows, label):
    for row_label, value, _ in rows:
        if row_label == label:
            return value
    raise AssertionError("no row %r in %r" % (label, [r[0] for r in rows]))


def state_of(rows, label):
    for row_label, _, state in rows:
        if row_label == label:
            return state
    raise AssertionError("no row %r" % label)


# --- Spotify --------------------------------------------------------------

def snapshot(**overrides):
    snap = {"token": "t", "token_error": None, "devices": [],
            "devices_error": None, "playback": None, "playback_error": None}
    snap.update(overrides)
    return snap


def test_device_missing_from_spotify_is_the_headline_failure(monkeypatch):
    """spotifyd can be 'running' with no device registered - item 29's state"""
    monkeypatch.setenv("SPOTIFY_DEVICE_ID", "ours")
    rows = status_web.spotify_rows(snapshot(devices=[{"id": "a_phone",
                                                      "name": "iPhone"}]))
    assert state_of(rows, "Visible to Spotify") == status_web.BAD
    assert "1 other device visible" in value_of(rows, "Visible to Spotify")


def test_device_present_reports_its_name_and_volume(monkeypatch):
    monkeypatch.setenv("SPOTIFY_DEVICE_ID", "ours")
    rows = status_web.spotify_rows(snapshot(devices=[
        {"id": "ours", "name": "toem2", "volume_percent": 55}]))
    assert state_of(rows, "Visible to Spotify") == status_web.OK
    assert "toem2" in value_of(rows, "Visible to Spotify")
    assert value_of(rows, "Spotify volume") == "55 %"


def test_no_token_says_reauthorization_and_shows_the_reason():
    rows = status_web.spotify_rows(
        snapshot(token=None, token_error="invalid_grant"))
    assert state_of(rows, "Spotify connection") == status_web.BAD
    assert value_of(rows, "Reported by Spotify") == "invalid_grant"
    # Without a token there is nothing to ask about the device list.
    assert all(label != "Visible to Spotify" for label, _, _ in rows)


def test_token_expiry_unknown_is_not_reported_as_expired(monkeypatch):
    monkeypatch.delenv("SPOTIFY_AUTH_DATE", raising=False)
    label, value, state = status_web.token_expiry_row()
    assert state == status_web.FLAT
    assert "unknown" in value


@pytest.mark.parametrize("days_ago, expected", [
    (0, status_web.OK),
    (170, status_web.WARN),   # inside the 30-day warning window
    (200, status_web.BAD),    # past the 183-day lifetime
])
def test_token_expiry_states(monkeypatch, days_ago, expected):
    issued = datetime.date.today() - datetime.timedelta(days=days_ago)
    monkeypatch.setenv("SPOTIFY_AUTH_DATE", issued.isoformat())
    assert status_web.token_expiry_row()[2] == expected


def test_snapshot_survives_a_broken_device_list(monkeypatch):
    """One failing call must not cost the page the other one"""
    monkeypatch.setattr(status_web.spotify, "get_auth_manager",
                        lambda: MagicMock(get_token=lambda: "t", rejected_at=0))

    def fake_get(token, path, timeout=status_web.API_TIMEOUT):
        if "devices" in path:
            raise RuntimeError("boom")
        return {"is_playing": True}

    monkeypatch.setattr(status_web, "api_get", fake_get)
    snap = status_web.spotify_snapshot()
    assert snap["devices_error"] == "boom"
    assert snap["playback"] == {"is_playing": True}


# --- what is playing ------------------------------------------------------

def test_local_playback_beats_spotify(monkeypatch):
    monkeypatch.setattr(status_web, "sh",
                        lambda c, timeout=4: "Anne Kaffeekanne\n[playing] #3/12")
    label, value, state = status_web.now_playing_row(snapshot())
    assert value.startswith("local: Anne Kaffeekanne")
    assert state == status_web.OK


def test_playback_on_a_phone_is_not_reported_as_ours(monkeypatch):
    monkeypatch.setenv("SPOTIFY_DEVICE_ID", "ours")
    snap = snapshot(playback={"is_playing": True,
                              "device": {"id": "phone", "name": "iPhone"},
                              "item": {"name": "Folge 17"}})
    label, value, state = status_web.now_playing_row(snap)
    assert "nothing here" in value and "iPhone" in value
    assert state == status_web.FLAT


def test_playing_on_our_device_names_artist_and_track(monkeypatch):
    monkeypatch.setenv("SPOTIFY_DEVICE_ID", "ours")
    snap = snapshot(playback={
        "is_playing": True, "device": {"id": "ours"},
        "item": {"name": "Kapitel 02", "artists": [{"name": "Bibi"}]}})
    assert status_web.now_playing_row(snap)[1] == "Spotify: Bibi - Kapitel 02"


# --- the loaded card ------------------------------------------------------

def test_no_card_since_startup(app):
    rows = status_web.loaded_card_rows(app, snapshot())
    assert "none" in value_of(rows, "Card loaded")


def test_loaded_card_shows_title_and_saved_position(app):
    app.get_player = lambda: SimpleNamespace(
        rfid="0001", is_series=False,
        playback_state={"offset": {"position": 2}, "position_ms": 64429})
    rows = status_web.loaded_card_rows(app, snapshot())
    assert value_of(rows, "Card loaded") == "Bibi Blocksberg (0001)"
    assert value_of(rows, "Saved position") == "track 3 at 1:04"


def test_series_card_shows_the_episode(app):
    app.get_player = lambda: SimpleNamespace(
        rfid="0001", is_series=True, episode_index=2,
        episodes=[{"title": "one"}, {"title": "two"}, {"title": "three"}],
        current_episode=lambda: {"title": "three"},
        playback_state={"offset": {"position": 0}, "position_ms": 0})
    rows = status_web.loaded_card_rows(app, snapshot())
    assert value_of(rows, "Episode") == "3 of 3 - three"


def test_foreign_session_explains_why_the_buttons_do_nothing(app, monkeypatch):
    monkeypatch.setenv("SPOTIFY_DEVICE_ID", "ours")
    app.get_player = lambda: SimpleNamespace(
        rfid="0001", is_series=False, playback_state={})
    snap = snapshot(playback={"device": {"id": "phone", "name": "iPhone"}})
    rows = status_web.loaded_card_rows(app, snap)
    assert state_of(rows, "Spotify session") == status_web.WARN
    assert "iPhone" in value_of(rows, "Spotify session")


# --- the player and its database -----------------------------------------

def test_card_counts_come_from_the_database(app, monkeypatch):
    monkeypatch.setenv("ENABLE_SYNC", "false")
    rows = status_web.card_rows(app)
    value = value_of(rows, "Cards registered")
    assert value.startswith("2 (")
    assert "1 spotify" in value and "1 local" in value


def test_sync_that_has_never_run_is_a_warning(app, monkeypatch):
    monkeypatch.setenv("ENABLE_SYNC", "true")
    assert state_of(status_web.card_rows(app), "New cards synced") == status_web.WARN


def test_database_read_never_uses_the_app_connection(app):
    """The page must open its own connection, whatever thread it runs on"""
    assert status_web.db_query(app.database_url, "SELECT 1") == [(1,)]
    assert status_web.db_query("/nonexistent.db", "SELECT 1") is None


def test_missing_card_reader_is_reported_as_broken(app, monkeypatch):
    monkeypatch.delenv("DEVELOPMENT", raising=False)
    app.rfid_reader = None
    rows = status_web.player_rows(app)
    assert state_of(rows, "Card reader") == status_web.BAD


def test_development_mode_says_the_pi_stays_on(app, monkeypatch):
    """The zombie state: the player exits, the Pi does not switch off"""
    monkeypatch.setenv("DEVELOPMENT", "true")
    assert "Pi stays on" in value_of(status_web.player_rows(app), "Idle timeout")


def test_page_served_without_a_player_says_so(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", str(tmp_path / "missing.db"))
    rows = status_web.player_rows(None)
    assert state_of(rows, "Player program") == status_web.BAD


# --- the log --------------------------------------------------------------

def test_log_marks_errors_and_escapes_html(app):
    with open(app.log_path, "w") as handle:
        handle.write("2026-08-25 [INFO] Scanned RFID: <0001>\n"
                     "2026-08-25 [ERROR] Could not start playback\n")
    rendered = status_web.log_html(app)
    assert "&lt;0001&gt;" in rendered
    assert "<span class='logbad'>2026-08-25 [ERROR]" in rendered


def test_missing_log_is_not_an_error(app):
    assert "Nothing recorded yet" in status_web.log_html(app)


# --- the server -----------------------------------------------------------

def test_get_serves_the_page_and_counts_as_activity(app, monkeypatch):
    monkeypatch.setattr(status_web, "spotify_snapshot", snapshot)
    touched = []
    app.reset_last_activity = lambda: touched.append(True)

    server = status_web.start(app, port=0)
    try:
        url = "http://127.0.0.1:%d/" % server.server_address[1]
        with urllib.request.urlopen(url, timeout=5) as response:
            body = response.read().decode()
            assert response.status == 200
        assert "<h2>Spotify</h2>" in body and "<h2>Device</h2>" in body
        # Someone is standing at the device reading this; it must not switch
        # itself off mid-diagnosis.
        assert touched

        with pytest.raises(urllib.error.HTTPError) as raised:
            urllib.request.urlopen(url + "elsewhere", timeout=5)
        assert raised.value.code == 404
    finally:
        server.shutdown()
        server.server_close()


def test_a_failing_section_still_returns_a_page(app, monkeypatch):
    """A 500 with an empty body tells whoever opened it nothing"""
    def explode():
        raise RuntimeError("Spotify unreachable")

    monkeypatch.setattr(status_web, "spotify_snapshot", explode)
    server = status_web.start(app, port=0)
    try:
        url = "http://127.0.0.1:%d/" % server.server_address[1]
        with pytest.raises(urllib.error.HTTPError) as raised:
            urllib.request.urlopen(url, timeout=5)
        assert raised.value.code == 500
        assert "Spotify unreachable" in raised.value.read().decode()
    finally:
        server.shutdown()
        server.server_close()


def test_port_zero_switches_the_page_off(app, monkeypatch):
    monkeypatch.setenv("STATUS_PORT", "0")
    assert status_web.start(app) is None
