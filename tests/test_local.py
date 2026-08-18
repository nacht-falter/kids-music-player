import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
import pytest
from unittest.mock import patch
from local import AudioPlayer

def setup_in_memory_db():
    import sqlite3
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

def test_audioplayer_initialization():
    db = None
    player = AudioPlayer("rfid1", None, "/music/track1.mp3")
    assert player.rfid == "rfid1"
    assert player.location == "/music/track1.mp3"
    assert player.playback_state == {"track": 1, "position": "0%"}
    assert not player.playing

    # With playback_state
    state = '{"track": 2, "position": "50%"}'
    player2 = AudioPlayer("rfid2", state, "/music/track2.mp3")
    assert player2.playback_state == {"track": 2, "position": "50%"}

def test_toggle_playback_and_pause():
    db = None
    player = AudioPlayer("rfid1", None, "/music/track1.mp3")
    with patch("os.system") as mock_system:
        player.toggle_playback()
        assert player.playing
        mock_system.assert_called_with("mpc -q toggle")
        player.pause_playback()
        assert not player.playing
        mock_system.assert_called_with("mpc -q pause")

def test_restart_playback():
    db = None
    player = AudioPlayer("rfid1", '{"track": 3, "position": "75%"}', "/music/track1.mp3")
    with patch("os.system") as mock_system:
        player.restart_playback()
        assert player.playback_state == {"track": 1, "position": "0%"}
        assert player.playing
        # play() is called, so mpc commands should be issued
        assert mock_system.call_count >= 3 

def test_check_playback_status_playing():
    db = None
    player = AudioPlayer("rfid1", None, "/music/track1.mp3")
    with patch("os.popen") as mock_popen:
        mock_popen.return_value.readlines.return_value = ["", "[playing] #1/10 3:45/45:12 (8%)"]
        player.check_playback_status()
        assert player.playing

def test_check_playback_status_paused():
    db = None
    player = AudioPlayer("rfid1", None, "/music/track1.mp3")
    with patch("os.popen") as mock_popen:
        mock_popen.return_value.readlines.return_value = ["", "[paused] #1/10 3:45/45:12 (8%)"]
        player.check_playback_status()
        assert not player.playing

def test_play():
    db = None
    player = AudioPlayer("rfid1", None, "/music/track1.mp3")
    with patch("os.system") as mock_system:
        player.play()
        assert player.playing
        # Should call mpc commands: clear/add, play track, seek position
        assert mock_system.call_count >= 3

def test_next_track():
    db = None
    player = AudioPlayer("rfid1", None, "/music/track1.mp3")
    with patch("os.system") as mock_system:
        player.next_track()
        assert player.playing
        mock_system.assert_called_with("mpc -q next")

def test_previous_track():
    db = None
    player = AudioPlayer("rfid1", None, "/music/track1.mp3")
    with patch("os.system") as mock_system:
        player.previous_track()
        assert player.playing
        mock_system.assert_called_with("mpc -q prev")

def test_save_playback_state(tmp_path, monkeypatch):
    # Must be a file, not :memory: - the state is persisted through a fresh
    # connection opened against DATABASE_URL, so it has to be reachable by path.
    import json
    import sqlite3

    database_url = str(tmp_path / "test.db")
    monkeypatch.setenv("DATABASE_URL", database_url)

    db = sqlite3.connect(database_url)
    db.execute("""
        CREATE TABLE music(
            rfid TEXT PRIMARY KEY,
            source TEXT NOT NULL,
            playback_state TEXT,
            location TEXT NOT NULL,
            title TEXT
        );
    """)
    db.execute(
        "INSERT INTO music (rfid, source, location, title) VALUES (?, ?, ?, ?)",
        ("rfid1", "local", "/music/track1.mp3", "Test Album")
    )
    db.commit()

    player = AudioPlayer("rfid1", None, "/music/track1.mp3")
    with patch("os.popen") as mock_popen:
        mock_popen.return_value.read.return_value = "3"
        mock_popen.return_value.readlines.return_value = ["", "track info (45%)"]
        with patch("re.search") as mock_search:
            mock_search.return_value.group.return_value = "45%"
            player.save_playback_state()

    # Read back through a separate connection: proves the write was committed,
    # not just left pending in an open transaction.
    verify = sqlite3.connect(database_url)
    result = verify.execute(
        "SELECT playback_state FROM music WHERE rfid = ?", ("rfid1",)).fetchone()
    verify.close()

    assert result is not None
    assert result[0] is not None, "playback_state was not persisted"
    # Both values come from the same mocked os.popen().read()
    assert json.loads(result[0]) == {"track": "3", "position": "3"} 

def test_previous_restarts_the_track_when_past_the_threshold():
    """Same rule as SpotifyPlayer, via mpc"""
    player = AudioPlayer("rfid1", None, "/music/track1.mp3")
    with patch("os.popen") as mock_popen, patch("os.system") as mock_system:
        mock_popen.return_value.readlines.return_value = [
            "Some Track", "[playing] #3/12   0:47/3:21 (23%)"]
        player.previous_track()
    mock_system.assert_called_with("mpc -q seek 0")


def test_previous_skips_back_near_the_start():
    player = AudioPlayer("rfid1", None, "/music/track1.mp3")
    with patch("os.popen") as mock_popen, patch("os.system") as mock_system:
        mock_popen.return_value.readlines.return_value = [
            "Some Track", "[playing] #3/12   0:01/3:21 (0%)"]
        player.previous_track()
    mock_system.assert_called_with("mpc -q prev")
