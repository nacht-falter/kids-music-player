import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
import pytest
from unittest.mock import patch, MagicMock
import json
import sqlite3
import requests

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

# --- SpotifyAuthManager tests ---
def test_spotify_auth_manager_init_success(monkeypatch):
    monkeypatch.setenv("SPOTIFY_USERCREDS", "test_creds")
    monkeypatch.setenv("SPOTIFY_REFRESH_TOKEN", "test_token")
    
    # Mock utils before importing spotify to avoid circular import
    with patch.dict('sys.modules', {'utils': MagicMock()}):
        from spotify import SpotifyAuthManager
        auth_manager = SpotifyAuthManager()
        assert auth_manager.usercreds == "test_creds"
        assert auth_manager.refresh_token == "test_token"

def test_spotify_auth_manager_missing_usercreds(monkeypatch):
    monkeypatch.delenv("SPOTIFY_USERCREDS", raising=False)
    monkeypatch.setenv("SPOTIFY_REFRESH_TOKEN", "test_token")
    
    # Mock utils before importing spotify to avoid circular import
    with patch.dict('sys.modules', {'utils': MagicMock()}):
        from spotify import SpotifyAuthManager
        with pytest.raises(ValueError, match="SPOTIFY_USERCREDS"):
            SpotifyAuthManager()

def test_spotify_auth_manager_missing_refresh_token(monkeypatch):
    monkeypatch.setenv("SPOTIFY_USERCREDS", "test_creds")
    monkeypatch.delenv("SPOTIFY_REFRESH_TOKEN", raising=False)
    
    # Mock utils before importing spotify to avoid circular import
    with patch.dict('sys.modules', {'utils': MagicMock()}):
        from spotify import SpotifyAuthManager
        with pytest.raises(ValueError, match="SPOTIFY_REFRESH_TOKEN"):
            SpotifyAuthManager()

def test_spotify_auth_manager_get_token_success(monkeypatch):
    monkeypatch.setenv("SPOTIFY_USERCREDS", "test_creds")
    monkeypatch.setenv("SPOTIFY_REFRESH_TOKEN", "test_token")
    
    # Mock utils before importing spotify to avoid circular import
    with patch.dict('sys.modules', {'utils': MagicMock()}):
        from spotify import SpotifyAuthManager
        auth_manager = SpotifyAuthManager()
        
        # Mock only the post method
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "access_token": "new_token",
            "expires_in": 3600
        }
        mock_response.raise_for_status.return_value = None
        with patch('spotify.requests.post', return_value=mock_response):
            token = auth_manager.get_token()
            assert token == "new_token"

def test_spotify_auth_manager_token_refresh_failure(monkeypatch):
    monkeypatch.setenv("SPOTIFY_USERCREDS", "test_creds")
    monkeypatch.setenv("SPOTIFY_REFRESH_TOKEN", "test_token")
    
    # Mock utils before importing spotify to avoid circular import
    with patch.dict('sys.modules', {'utils': MagicMock()}):
        from spotify import SpotifyAuthManager
        auth_manager = SpotifyAuthManager()
        
        # Mock only the post method with the correct exception type
        with patch('spotify.requests.post', side_effect=requests.RequestException("Network error")):
            token = auth_manager.get_token()
            assert token is None

# --- SpotifyPlayer tests ---
def test_spotify_player_init_success(monkeypatch):
    monkeypatch.setenv("SPOTIFY_DEVICE_ID", "test_device")
    db = setup_in_memory_db()
    
    # Mock utils before importing spotify to avoid circular import
    with patch.dict('sys.modules', {'utils': MagicMock()}):
        from spotify import SpotifyPlayer
        player = SpotifyPlayer("rfid123", None, "spotify:album:123")
        assert player.rfid == "rfid123"
        assert player.location == "spotify:album:123"
        assert player.device_id == "test_device"
        assert not player.playing

def test_spotify_player_init_with_playback_state(monkeypatch):
    monkeypatch.setenv("SPOTIFY_DEVICE_ID", "test_device")
    db = setup_in_memory_db()
    playback_state = '{"offset": {"position": 5}, "position_ms": 30000}'
    
    # Mock utils before importing spotify to avoid circular import
    with patch.dict('sys.modules', {'utils': MagicMock()}):
        from spotify import SpotifyPlayer
        player = SpotifyPlayer("rfid123", playback_state, "spotify:album:123")
        assert player.playback_state["offset"]["position"] == 5
        assert player.playback_state["position_ms"] == 30000

def test_spotify_player_missing_device_id(monkeypatch):
    monkeypatch.delenv("SPOTIFY_DEVICE_ID", raising=False)
    db = setup_in_memory_db()
    
    # Mock utils before importing spotify to avoid circular import
    with patch.dict('sys.modules', {'utils': MagicMock()}):
        from spotify import SpotifyPlayer
        with pytest.raises(ValueError, match="SPOTIFY_DEVICE_ID"):
            SpotifyPlayer("rfid123", None, "spotify:album:123")

def test_spotify_player_transfer_playback_success(monkeypatch):
    monkeypatch.setenv("SPOTIFY_DEVICE_ID", "test_device")
    db = setup_in_memory_db()
    
    # Mock utils before importing spotify to avoid circular import
    with patch.dict('sys.modules', {'utils': MagicMock()}):
        from spotify import SpotifyPlayer
        player = SpotifyPlayer("rfid123", None, "spotify:album:123")
        
        # Mock only the put method
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        with patch('spotify.requests.put', return_value=mock_response):
            result = player.transfer_playback(play=False)
            assert result is True

def test_spotify_player_transfer_playback_failure(monkeypatch):
    monkeypatch.setenv("SPOTIFY_DEVICE_ID", "test_device")
    db = setup_in_memory_db()
    
    # Mock utils before importing spotify to avoid circular import
    with patch.dict('sys.modules', {'utils': MagicMock()}):
        from spotify import SpotifyPlayer
        player = SpotifyPlayer("rfid123", None, "spotify:album:123")
        
        # Mock only the put method with the correct exception type
        with patch('spotify.requests.put', side_effect=requests.RequestException("Network error")):
            result = player.transfer_playback(play=False)
            assert result is False

def test_spotify_player_is_ready_true(monkeypatch):
    monkeypatch.setenv("SPOTIFY_DEVICE_ID", "test_device")
    db = setup_in_memory_db()
    
    # Mock utils before importing spotify to avoid circular import
    with patch.dict('sys.modules', {'utils': MagicMock()}):
        from spotify import SpotifyPlayer
        player = SpotifyPlayer("rfid123", None, "spotify:album:123")
        player.active_device = "test_device"
        
        mock_playback = {"device": {"id": "test_device"}}
        with patch.object(player, "check_playback_status", return_value=mock_playback):
            assert player.is_ready() is True

def test_spotify_player_is_ready_false(monkeypatch):
    monkeypatch.setenv("SPOTIFY_DEVICE_ID", "test_device")
    db = setup_in_memory_db()
    
    # Mock utils before importing spotify to avoid circular import
    with patch.dict('sys.modules', {'utils': MagicMock()}):
        from spotify import SpotifyPlayer
        player = SpotifyPlayer("rfid123", None, "spotify:album:123")
        player.active_device = "other_device"
        
        mock_playback = {"device": {"id": "other_device"}}
        with patch.object(player, "check_playback_status", return_value=mock_playback):
            assert player.is_ready() is False

def test_spotify_player_play_success(monkeypatch):
    monkeypatch.setenv("SPOTIFY_DEVICE_ID", "test_device")
    db = setup_in_memory_db()
    
    # Mock utils before importing spotify to avoid circular import
    with patch.dict('sys.modules', {'utils': MagicMock()}):
        from spotify import SpotifyPlayer
        player = SpotifyPlayer("rfid123", None, "spotify:album:123")
        
        # Mock only the put method
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        with patch('spotify.requests.put', return_value=mock_response), \
             patch('spotify.utils.play_sound'):
            player.play()
            assert player.playing is True
            assert player.playback_started is True
            assert player.active_device == "test_device"

def test_spotify_player_play_failure(monkeypatch):
    monkeypatch.setenv("SPOTIFY_DEVICE_ID", "test_device")
    db = setup_in_memory_db()
    
    # Mock utils before importing spotify to avoid circular import
    with patch.dict('sys.modules', {'utils': MagicMock()}):
        from spotify import SpotifyPlayer
        player = SpotifyPlayer("rfid123", None, "spotify:album:123")
        
        # Mock only the put method with the correct exception type
        with patch('spotify.requests.put', side_effect=requests.RequestException("Network error")), \
             patch('spotify.utils.play_sound') as mock_sound:
            player.play()
            mock_sound.assert_called_with("playback_error")

def test_spotify_player_toggle_playback_playing_to_pause(monkeypatch):
    monkeypatch.setenv("SPOTIFY_DEVICE_ID", "test_device")
    db = setup_in_memory_db()
    
    # Mock utils before importing spotify to avoid circular import
    with patch.dict('sys.modules', {'utils': MagicMock()}):
        from spotify import SpotifyPlayer
        player = SpotifyPlayer("rfid123", None, "spotify:album:123")
        player.playing = True
        
        # Mock only the put method
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        with patch('spotify.requests.put', return_value=mock_response):
            player.toggle_playback()
            assert player.playing is False

def test_spotify_player_toggle_playback_paused_to_resume(monkeypatch):
    monkeypatch.setenv("SPOTIFY_DEVICE_ID", "test_device")
    db = setup_in_memory_db()
    
    # Mock utils before importing spotify to avoid circular import
    with patch.dict('sys.modules', {'utils': MagicMock()}):
        from spotify import SpotifyPlayer
        player = SpotifyPlayer("rfid123", None, "spotify:album:123")
        player.playing = False
        
        # Mock only the put method
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        with patch('spotify.requests.put', return_value=mock_response):
            player.toggle_playback()
            assert player.playing is True

def _playback_status(device_id, context_uri, is_playing=True):
    """Build a mocked GET /me/player response"""
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {
        "device": {"id": device_id},
        "context": {"uri": context_uri},
        "is_playing": is_playing,
    }
    return response


def test_spotify_player_next_track_success(monkeypatch):
    monkeypatch.setenv("SPOTIFY_DEVICE_ID", "test_device")
    db = setup_in_memory_db()

    # Mock utils before importing spotify to avoid circular import
    with patch.dict('sys.modules', {'utils': MagicMock()}):
        from spotify import SpotifyPlayer
        player = SpotifyPlayer("rfid123", None, "spotify:album:123")

        # next_track refreshes state first, so the status GET must be mocked
        # too - our device, playing our album.
        status = _playback_status("test_device", "spotify:album:123")
        with patch.object(player.auth_manager, "get_token", return_value="tok"), \
                patch('spotify.requests.get', return_value=status), \
                patch('spotify.requests.post') as mock_post:
            player.next_track()
            mock_post.assert_called_once()
            assert player.playing

def test_spotify_player_next_track_reclaims_when_not_ours(monkeypatch):
    """Another device holds the session: reclaim our album, do not skip on it"""
    monkeypatch.setenv("SPOTIFY_DEVICE_ID", "test_device")
    db = setup_in_memory_db()

    # Mock utils before importing spotify to avoid circular import
    with patch.dict('sys.modules', {'utils': MagicMock()}):
        from spotify import SpotifyPlayer
        player = SpotifyPlayer("rfid123", None, "spotify:album:123")

        status = _playback_status("other_device", "spotify:album:999")
        with patch.object(player.auth_manager, "get_token", return_value="tok"), \
                patch('spotify.requests.get', return_value=status), \
                patch('spotify.requests.put') as mock_put, \
                patch('spotify.requests.post') as mock_post:
            player.next_track()
            # No skip issued against the foreign session ...
            mock_post.assert_not_called()
            # ... instead our own album is played explicitly.
            mock_put.assert_called_once()
            assert mock_put.call_args.kwargs["json"]["context_uri"] == "spotify:album:123"

def test_spotify_player_toggle_does_not_inherit_foreign_content(monkeypatch):
    """A podcast on a phone must never be resumed by pressing play here"""
    monkeypatch.setenv("SPOTIFY_DEVICE_ID", "test_device")
    db = setup_in_memory_db()

    with patch.dict('sys.modules', {'utils': MagicMock()}):
        from spotify import SpotifyPlayer
        player = SpotifyPlayer("rfid123", None, "spotify:album:123")

        # This device was handed someone else's episode, currently paused.
        status = _playback_status(
            "test_device", "spotify:show:podcast", is_playing=False)
        with patch.object(player.auth_manager, "get_token", return_value="tok"), \
                patch('spotify.requests.get', return_value=status), \
                patch('spotify.requests.put') as mock_put:
            player.toggle_playback()
            # play() with our context, never a context-less resume.
            mock_put.assert_called_once()
            assert mock_put.call_args.kwargs["json"]["context_uri"] == "spotify:album:123"

# --- get_auth_manager tests ---
def test_get_auth_manager_singleton(monkeypatch):
    monkeypatch.setenv("SPOTIFY_USERCREDS", "test_creds")
    monkeypatch.setenv("SPOTIFY_REFRESH_TOKEN", "test_token")
    
    # Mock utils before importing spotify to avoid circular import
    with patch.dict('sys.modules', {'utils': MagicMock()}):
        from spotify import get_auth_manager
        
        # Clear the global variable
        import spotify
        spotify._auth_manager = None
        
        auth1 = get_auth_manager()
        auth2 = get_auth_manager()
        assert auth1 is auth2 