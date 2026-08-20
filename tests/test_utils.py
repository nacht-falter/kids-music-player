import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
import sqlite3
import pytest
from unittest.mock import patch, MagicMock
from utils import get_music_data, create_player, play_sound, save_last_played, get_last_played_rfid, handle_already_playing, shutdown, verify_env_file
import utils

def setup_in_memory_db():
    db = sqlite3.connect(":memory:")
    cursor = db.cursor()
    cursor.execute("""
        CREATE TABLE music(
            rfid TEXT PRIMARY KEY,
            source TEXT NOT NULL,
            playback_state TEXT,
            location TEXT NOT NULL,
            title TEXT
        );
    """)
    db.commit()
    return db

def test_get_music_data_found():
    db = setup_in_memory_db()
    cursor = db.cursor()
    cursor.execute(
        "INSERT INTO music (rfid, source, playback_state, location, title) VALUES (?, ?, ?, ?, ?)",
        ("123456", "local", None, "/music/album1", "Test Album")
    )
    db.commit()
    result = get_music_data(db, "123456")
    assert result is not None
    assert result["rfid"] == "123456"
    assert result["source"] == "local"
    assert result["location"] == "/music/album1"
    assert result["title"] == "Test Album"

def test_get_music_data_not_found():
    db = setup_in_memory_db()
    result = get_music_data(db, "notfound")
    assert result is None

# --- create_player tests ---
def test_create_player_local(monkeypatch):
    class DummyAudioPlayer:
        def __init__(self, rfid, playback_state, location):
            self.rfid = rfid
            self.playback_state = playback_state
            self.location = location
    monkeypatch.setattr("utils.AudioPlayer", DummyAudioPlayer)
    music_data = {"rfid": "abc", "source": "local", "playback_state": None, "location": "/music/abc"}
    player = create_player(music_data)
    assert isinstance(player, DummyAudioPlayer)
    assert player.rfid == "abc"
    assert player.location == "/music/abc"

def test_create_player_spotify(monkeypatch):
    class DummySpotifyPlayer:
        def __init__(self, rfid, playback_state, location):
            self.rfid = rfid
            self.playback_state = playback_state
            self.location = location
            self._ready = True
        def transfer_playback(self, play):
            # Mirrors the real contract: True on success, False on failure.
            # create_player now relies on this, so a double returning None
            # silently looks like a failed transfer.
            return True
        def is_ready(self):
            return self._ready
    
    # Mock the spotify module import inside create_player
    with patch('builtins.__import__') as mock_import:
        def mock_import_func(name, *args, **kwargs):
            if name == 'spotify':
                mock_spotify = MagicMock()
                mock_spotify.SpotifyPlayer = DummySpotifyPlayer
                return mock_spotify
            return __import__(name, *args, **kwargs)
        
        mock_import.side_effect = mock_import_func
        
        music_data = {"rfid": "xyz", "source": "spotify", "playback_state": None, "location": "spotify:track:xyz"}
        player = create_player(music_data, retries=1)
        assert isinstance(player, DummySpotifyPlayer)
        assert player.rfid == "xyz"
        assert player.location == "spotify:track:xyz"

def test_create_player_unknown_source():
    music_data = {"rfid": "nope", "source": "unknown", "playback_state": None, "location": "foo"}
    player = create_player(music_data)
    assert player is None

# --- play_sound tests ---
def test_play_sound_runs(monkeypatch):
    with patch("subprocess.Popen") as mock_popen:
        play_sound("start")
        assert mock_popen.called

def test_play_sound_blocking(monkeypatch):
    with patch("subprocess.run") as mock_run:
        play_sound("shutdown", blocking=True)
        assert mock_run.called

def test_play_sound_unknown_event():
    with pytest.raises(ValueError):
        play_sound("not_an_event")

def setup_last_played_db():
    db = sqlite3.connect(":memory:")
    cursor = db.cursor()
    cursor.execute("""
        CREATE TABLE last_played (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            last_played_rfid TEXT
        );
    """
    )
    db.commit()
    return db

def test_save_and_get_last_played():
    db = setup_last_played_db()
    save_last_played(db, "rfid123")
    rfid = get_last_played_rfid(db)
    assert rfid == "rfid123"

def test_get_last_played_none():
    db = setup_last_played_db()
    rfid = get_last_played_rfid(db)
    assert rfid is None

def test_handle_already_playing_restart():
    player = MagicMock()
    player.is_series = False
    player.playing = True
    handle_already_playing(player)
    player.restart_playback.assert_called_once()
    player.toggle_playback.assert_not_called()

def test_handle_already_playing_toggle():
    player = MagicMock()
    player.playing = False
    handle_already_playing(player)
    player.toggle_playback.assert_called_once()
    player.restart_playback.assert_not_called()

def test_shutdown_calls(monkeypatch):
    # Explicit: this asserts the production branch, so DEVELOPMENT must be off.
    monkeypatch.delenv("DEVELOPMENT", raising=False)
    player = MagicMock()
    sync_done = MagicMock()
    sync_done.is_set.return_value = True
    with patch("utils.play_sound") as mock_sound, \
         patch("os.system") as mock_system, \
         patch("logging.shutdown") as mock_log_shutdown:
        shutdown(player, sync_done)
        mock_sound.assert_called_with("shutdown", blocking=True)
        player.pause_playback.assert_called_once()
        player.save_playback_state.assert_called_once()
        mock_log_shutdown.assert_called_once()
        # Should call os.system for shutdown if not DEVELOPMENT
        mock_system.assert_called_with("sudo shutdown -h now")

def test_shutdown_development(monkeypatch):
    player = MagicMock()
    sync_done = MagicMock()
    sync_done.is_set.return_value = True
    monkeypatch.setenv("DEVELOPMENT", "true")
    with patch("utils.play_sound"), \
         patch("os._exit") as mock_exit, \
         patch("logging.shutdown"):
        shutdown(player, sync_done)
        mock_exit.assert_called_once()
    monkeypatch.delenv("DEVELOPMENT")

def test_shutdown_waits_for_sync():
    player = MagicMock()
    sync_done = MagicMock()
    sync_done.is_set.return_value = False
    with patch("utils.play_sound"), \
         patch("os.system"), \
         patch("logging.shutdown"):
        shutdown(player, sync_done)
        sync_done.wait.assert_called()

def test_verify_env_file_success():
    config = {
        "SPOTIFY_USERCREDS": "x",
        "SPOTIFY_REFRESH_TOKEN": "x",
        "SPOTIFY_DEVICE_ID": "x",
        "DATABASE_URL": "x",
        "RFID_READER": "x"
    }
    verify_env_file(config)

def test_verify_env_file_missing(monkeypatch):
    config = {
        "SPOTIFY_USERCREDS": "x",
        "SPOTIFY_REFRESH_TOKEN": "x",
        "SPOTIFY_DEVICE_ID": "x",
        "DATABASE_URL": "x"
        # Missing RFID_READER
    }
    with pytest.raises(ValueError):
        verify_env_file(config)

def test_verify_env_file_enable_sync_missing():
    config = {
        "SPOTIFY_USERCREDS": "x",
        "SPOTIFY_REFRESH_TOKEN": "x",
        "SPOTIFY_DEVICE_ID": "x",
        "DATABASE_URL": "x",
        "RFID_READER": "x",
        "ENABLE_SYNC": "true"
        # Missing SYNC_API_URL and SYNC_API_TOKEN
    }
    with pytest.raises(ValueError):
        verify_env_file(config) 
def test_create_player_fails_fast_on_permanent_auth_error(monkeypatch):
    """Rejected credentials must not cost the full retry budget of silence"""
    import spotify

    def boom(*args, **kwargs):
        raise spotify.SpotifyAuthError("rejected", permanent=True)

    monkeypatch.setattr(spotify.SpotifyPlayer, "transfer_playback", boom)
    monkeypatch.setenv("SPOTIFY_DEVICE_ID", "test_device")
    music_data = {"rfid": "abc", "source": "spotify",
                  "playback_state": None, "location": "spotify:album:x"}

    with patch("utils.time.sleep") as mock_sleep:
        assert create_player(music_data, retries=10) is None
        # Returned on the first attempt, without waiting between retries.
        mock_sleep.assert_not_called()

def test_create_player_still_retries_transient_failures(monkeypatch):
    """Only auth failures short-circuit; a slow spotifyd still gets its retries"""
    import spotify

    monkeypatch.setattr(spotify.SpotifyPlayer, "transfer_playback",
                        lambda self, play=False: False)
    monkeypatch.setattr(spotify.SpotifyPlayer, "is_ready", lambda self: False)
    monkeypatch.setenv("SPOTIFY_DEVICE_ID", "test_device")
    music_data = {"rfid": "abc", "source": "spotify",
                  "playback_state": None, "location": "spotify:album:x"}

    with patch("utils.time.sleep") as mock_sleep:
        assert create_player(music_data, retries=3) is None
        assert mock_sleep.call_count == 3


def test_handle_already_playing_refreshes_before_branching():
    """A stale `playing` flag sent a dead device down the restart path"""
    player = MagicMock()
    player.is_series = False
    player.playing = True
    handle_already_playing(player)
    player.check_playback_status.assert_called_once()


def test_handle_already_playing_uses_refreshed_value():
    """check_playback_status may flip `playing`; the branch must follow it"""
    player = MagicMock()
    player.is_series = False
    player.playing = True

    def refresh():
        player.playing = False   # e.g. spotifyd died, device reports 204

    player.check_playback_status.side_effect = refresh
    handle_already_playing(player)

    player.toggle_playback.assert_called_once()
    player.restart_playback.assert_not_called()


def test_create_player_skips_readiness_check_after_failed_transfer():
    """is_ready() is a second round trip confirming what we already know"""
    import spotify
    calls = {"is_ready": 0}

    def failed_transfer(self, play=False):
        return False

    def counted_is_ready(self):
        calls["is_ready"] += 1
        return False

    with patch.object(spotify.SpotifyPlayer, "transfer_playback", failed_transfer), \
            patch.object(spotify.SpotifyPlayer, "is_ready", counted_is_ready), \
            patch.dict(os.environ, {"SPOTIFY_DEVICE_ID": "d"}), \
            patch("utils.time.sleep"):
        music_data = {"rfid": "abc", "source": "spotify",
                      "playback_state": None, "location": "spotify:album:x"}
        assert create_player(music_data, retries=3) is None

    assert calls["is_ready"] == 0


def test_create_player_still_checks_readiness_after_successful_transfer():
    import spotify
    calls = {"is_ready": 0}

    def ok_transfer(self, play=False):
        return True

    def ready(self):
        calls["is_ready"] += 1
        return True

    with patch.object(spotify.SpotifyPlayer, "transfer_playback", ok_transfer), \
            patch.object(spotify.SpotifyPlayer, "is_ready", ready), \
            patch.dict(os.environ, {"SPOTIFY_DEVICE_ID": "d"}):
        music_data = {"rfid": "abc", "source": "spotify",
                      "playback_state": None, "location": "spotify:album:x"}
        assert create_player(music_data, retries=3) is not None

    assert calls["is_ready"] == 1


def test_create_player_retries_when_the_token_is_merely_unavailable(monkeypatch):
    """No network at boot is transient - giving up in 6s broke card scans

    Observed on toem2 2026-08-17 15:50: wifi was still associating, the token
    request failed, and the scan errored after 6.1s on a device where nothing
    was wrong.
    """
    import spotify
    attempts = {"n": 0}

    def no_token_yet(self, play=False):
        attempts["n"] += 1
        raise spotify.SpotifyAuthError("could not reach Spotify", permanent=False)

    with patch.object(spotify.SpotifyPlayer, "transfer_playback", no_token_yet), \
            patch.dict(os.environ, {"SPOTIFY_DEVICE_ID": "d"}), \
            patch("utils.time.sleep"):
        music_data = {"rfid": "abc", "source": "spotify",
                      "playback_state": None, "location": "spotify:album:x"}
        assert create_player(music_data, retries=6) is None

    assert attempts["n"] == 6, "should use the whole budget, not abort"


# --- series episode cache ----------------------------------------------------

class TestSeriesCache:
    """Paging a long series is too slow to repeat on every scan

    Keyed by the playlist's snapshot_id, which Spotify changes whenever the
    playlist is edited, so a stale map is never served.
    """

    def test_round_trip(self, tmp_path, monkeypatch):
        monkeypatch.setattr(utils, "SERIES_CACHE_PATH",
                            str(tmp_path / "series.json"))
        episodes = [{"uri": "spotify:album:a", "durations": [1000]}]
        utils.write_series_cache("pl1", "snap1", episodes)

        cached = utils.read_series_cache("pl1")
        assert cached == {"snapshot_id": "snap1", "episodes": episodes}

    def test_unknown_playlist_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr(utils, "SERIES_CACHE_PATH",
                            str(tmp_path / "series.json"))
        utils.write_series_cache("pl1", "snap1", [])
        assert utils.read_series_cache("other") is None

    def test_several_playlists_coexist(self, tmp_path, monkeypatch):
        monkeypatch.setattr(utils, "SERIES_CACHE_PATH",
                            str(tmp_path / "series.json"))
        utils.write_series_cache("pl1", "snap1", [{"uri": "a"}])
        utils.write_series_cache("pl2", "snap2", [{"uri": "b"}])

        assert utils.read_series_cache("pl1")["snapshot_id"] == "snap1"
        assert utils.read_series_cache("pl2")["snapshot_id"] == "snap2"

    def test_missing_file_is_not_an_error(self, tmp_path, monkeypatch):
        monkeypatch.setattr(utils, "SERIES_CACHE_PATH",
                            str(tmp_path / "absent.json"))
        assert utils.read_series_cache("pl1") is None

    def test_corrupt_cache_is_not_an_error(self, tmp_path, monkeypatch):
        """Refetching is slower but always correct; crashing is not an option"""
        path = tmp_path / "series.json"
        path.write_text("{ this is not json")
        monkeypatch.setattr(utils, "SERIES_CACHE_PATH", str(path))

        assert utils.read_series_cache("pl1") is None
        utils.write_series_cache("pl1", "snap1", [{"uri": "a"}])
        assert utils.read_series_cache("pl1")["snapshot_id"] == "snap1"


def test_rescanning_a_playing_series_advances_an_episode():
    """Re-scanning means "go round again" - the next episode for a series"""
    player = MagicMock()
    player.is_series = True
    player.playing = True

    handle_already_playing(player)

    player.next_episode.assert_called_once()
    player.restart_playback.assert_not_called()


def test_rescanning_a_playing_album_still_restarts():
    """The same gesture, for a card with only one place to go"""
    player = MagicMock()
    player.is_series = False
    player.playing = True

    handle_already_playing(player)

    player.restart_playback.assert_called_once()
    player.next_episode.assert_not_called()


def test_rescanning_a_paused_series_resumes_rather_than_advancing():
    """Pausing and scanning again must not cost the child their episode"""
    player = MagicMock()
    player.is_series = True
    player.playing = False

    handle_already_playing(player)

    player.toggle_playback.assert_called_once()
    player.next_episode.assert_not_called()
