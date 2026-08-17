import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
import sqlite3
import pytest
from unittest.mock import patch, MagicMock
from utils import get_music_data, create_player, play_sound, save_last_played, get_last_played_rfid, handle_already_playing, shutdown, verify_env_file

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
            pass
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
def test_create_player_fails_fast_on_auth_error(monkeypatch):
    """A dead token must not cost the full retry budget of silence"""
    import spotify

    def boom(*args, **kwargs):
        raise spotify.SpotifyAuthError("no token")

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
    player.playing = True
    handle_already_playing(player)
    player.check_playback_status.assert_called_once()


def test_handle_already_playing_uses_refreshed_value():
    """check_playback_status may flip `playing`; the branch must follow it"""
    player = MagicMock()
    player.playing = True

    def refresh():
        player.playing = False   # e.g. spotifyd died, device reports 204

    player.check_playback_status.side_effect = refresh
    handle_already_playing(player)

    player.toggle_playback.assert_called_once()
    player.restart_playback.assert_not_called()
