import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
import pytest
from unittest.mock import patch, MagicMock
import json
import sqlite3
import time

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
        mock_response.status_code = 200
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
        with patch('spotify.requests.post', side_effect=requests.RequestException("Network error")), \
                patch('spotify.time.sleep') as mock_sleep:
            token = auth_manager.get_token()
            assert token is None
            # Backoff is exercised, but not actually waited through.
            assert mock_sleep.call_count == auth_manager.RETRIES - 1

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
def test_spotify_player_tracks_position_while_playing(monkeypatch):
    """Observing our own playback updates the in-memory position"""
    monkeypatch.setenv("SPOTIFY_DEVICE_ID", "test_device")

    with patch.dict('sys.modules', {'utils': MagicMock()}):
        from spotify import SpotifyPlayer
        stale = '{"offset": {"position": 1}, "position_ms": 48360}'
        player = SpotifyPlayer("rfid123", stale, "spotify:album:123")

        status = _playback_status("test_device", "spotify:album:123")
        status.json.return_value["progress_ms"] = 90000
        status.json.return_value["item"] = {"uri": "spotify:track:x",
                                            "track_number": 2}
        with patch.object(player.auth_manager, "get_token", return_value="tok"), \
                patch('spotify.requests.get', return_value=status):
            player.check_playback_status()

        assert player.playback_state == {
            "offset": {"position": 1}, "position_ms": 90000}

def test_spotify_player_reclaim_uses_fresh_position(monkeypatch):
    """A reclaim must not rewind to the position stored at card scan"""
    monkeypatch.setenv("SPOTIFY_DEVICE_ID", "test_device")

    with patch.dict('sys.modules', {'utils': MagicMock()}):
        from spotify import SpotifyPlayer
        stale = '{"offset": {"position": 1}, "position_ms": 48360}'
        player = SpotifyPlayer("rfid123", stale, "spotify:album:123")

        # While we still own playback, the position advances to 90s.
        ours = _playback_status("test_device", "spotify:album:123")
        ours.json.return_value["progress_ms"] = 90000
        ours.json.return_value["item"] = {"uri": "spotify:track:x",
                                          "track_number": 2}
        with patch.object(player.auth_manager, "get_token", return_value="tok"), \
                patch('spotify.requests.get', return_value=ours):
            player.refresh_playback_state()

        # A phone then takes the session and plays a standalone track.
        foreign = _playback_status("phone", None)
        with patch.object(player.auth_manager, "get_token", return_value="tok"), \
                patch('spotify.requests.get', return_value=foreign), \
                patch('spotify.requests.put') as mock_put:
            player.toggle_playback()

        sent = mock_put.call_args.kwargs["json"]
        assert sent["context_uri"] == "spotify:album:123"
        # 90000, not the 48360 it was constructed with.
        assert sent["position_ms"] == 90000

def test_save_playback_state_falls_back_when_not_ours(monkeypatch):
    """Transferring playback away then shutting down must not lose the session"""
    monkeypatch.setenv("SPOTIFY_DEVICE_ID", "test_device")

    with patch.dict('sys.modules', {'utils': MagicMock()}) as mods:
        from spotify import SpotifyPlayer
        known = '{"offset": {"position": 3}, "position_ms": 120000}'
        player = SpotifyPlayer("rfid123", known, "spotify:album:123")

        # A phone has taken the session and plays something else.
        foreign = _playback_status("phone", "spotify:album:999")
        with patch.object(player.auth_manager, "get_token", return_value="tok"), \
                patch('spotify.requests.get', return_value=foreign), \
                patch("spotify.utils.persist_playback_state") as mock_persist:
            player.save_playback_state()

        # The last position recorded while we owned playback is written,
        # not the phone's position and not nothing at all.
        mock_persist.assert_called_once_with(
            "rfid123", {"offset": {"position": 3}, "position_ms": 120000})

def test_refresh_playback_state_skips_unchanged(monkeypatch):
    """A paused player must not rewrite an identical row every tick"""
    monkeypatch.setenv("SPOTIFY_DEVICE_ID", "test_device")

    with patch.dict('sys.modules', {'utils': MagicMock()}):
        from spotify import SpotifyPlayer
        player = SpotifyPlayer("rfid123", None, "spotify:album:123")

        status = _playback_status("test_device", "spotify:album:123",
                                  is_playing=False)
        status.json.return_value["progress_ms"] = 5000
        status.json.return_value["item"] = {"uri": "spotify:track:x",
                                            "track_number": 1}
        with patch.object(player.auth_manager, "get_token", return_value="tok"), \
                patch('spotify.requests.get', return_value=status), \
                patch("spotify.utils.persist_playback_state") as mock_persist:
            assert player.refresh_playback_state() is True   # first: changed
            assert player.refresh_playback_state() is False  # second: identical
            assert mock_persist.call_count == 1

# --- auth failure handling ---
def _token_error(status, body):
    r = MagicMock()
    r.status_code = status
    r.text = body
    r.json.return_value = __import__("json").loads(body)
    r.raise_for_status.side_effect = requests.HTTPError("%d Client Error" % status)
    return r

def test_invalid_grant_is_not_retried(monkeypatch):
    """An expired refresh token is permanent; retrying only delays the failure"""
    monkeypatch.setenv("SPOTIFY_USERCREDS", "test_creds")
    monkeypatch.setenv("SPOTIFY_REFRESH_TOKEN", "test_token")

    with patch.dict('sys.modules', {'utils': MagicMock()}):
        from spotify import SpotifyAuthManager
        am = SpotifyAuthManager()
        resp = _token_error(400, '{"error":"invalid_grant",'
                                 '"error_description":"Refresh token revoked"}')
        with patch('spotify.requests.post', return_value=resp) as mock_post, \
                patch('spotify.time.sleep') as mock_sleep:
            assert am.get_token() is None
            mock_post.assert_called_once()      # not three times
            mock_sleep.assert_not_called()      # and no backoff

def test_transient_failure_is_still_retried(monkeypatch):
    monkeypatch.setenv("SPOTIFY_USERCREDS", "test_creds")
    monkeypatch.setenv("SPOTIFY_REFRESH_TOKEN", "test_token")

    with patch.dict('sys.modules', {'utils': MagicMock()}):
        from spotify import SpotifyAuthManager
        am = SpotifyAuthManager()
        with patch('spotify.requests.post',
                   side_effect=requests.RequestException("boom")) as mock_post, \
                patch('spotify.time.sleep'):
            assert am.get_token() is None
            assert mock_post.call_count == am.RETRIES

def test_missing_token_raises_instead_of_bearer_none(monkeypatch):
    """No token must not produce an 'Authorization: Bearer None' request"""
    monkeypatch.setenv("SPOTIFY_DEVICE_ID", "test_device")

    with patch.dict('sys.modules', {'utils': MagicMock()}):
        from spotify import SpotifyPlayer, SpotifyAuthError
        player = SpotifyPlayer("rfid123", None, "spotify:album:123")

        with patch.object(player.auth_manager, "get_token", return_value=None):
            with pytest.raises(SpotifyAuthError):
                player._get_headers()

            # And it is handled, not crashing, by the normal call paths.
            with patch('spotify.requests.get') as mock_get:
                assert player.check_playback_status() is None
                mock_get.assert_not_called()

def test_rejected_credentials_are_not_re_requested(monkeypatch):
    """A dead refresh token must not mean a token request per API call"""
    monkeypatch.setenv("SPOTIFY_USERCREDS", "test_creds")
    monkeypatch.setenv("SPOTIFY_REFRESH_TOKEN", "test_token")

    with patch.dict('sys.modules', {'utils': MagicMock()}):
        from spotify import SpotifyAuthManager
        am = SpotifyAuthManager()
        resp = _token_error(400, '{"error":"invalid_grant"}')
        with patch('spotify.requests.post', return_value=resp) as mock_post:
            for _ in range(10):
                assert am.get_token() is None
            mock_post.assert_called_once()

def test_cooldown_expires_and_recovery_clears_it(monkeypatch):
    monkeypatch.setenv("SPOTIFY_USERCREDS", "test_creds")
    monkeypatch.setenv("SPOTIFY_REFRESH_TOKEN", "test_token")

    with patch.dict('sys.modules', {'utils': MagicMock()}):
        from spotify import SpotifyAuthManager
        am = SpotifyAuthManager()
        bad = _token_error(400, '{"error":"invalid_grant"}')
        with patch('spotify.requests.post', return_value=bad):
            assert am.get_token() is None
        assert am.rejected_at

        # Pretend the cooldown elapsed and the token was fixed.
        am.rejected_at = time.time() - am.PERMANENT_FAILURE_COOLDOWN - 1
        good = MagicMock()
        good.status_code = 200
        good.json.return_value = {"access_token": "fresh", "expires_in": 3600}
        good.raise_for_status.return_value = None
        with patch('spotify.requests.post', return_value=good):
            assert am.get_token() == "fresh"
        assert am.rejected_at == 0

# --- context offset resolution ---
def _page(items, has_next=False):
    r = MagicMock()
    r.status_code = 200
    r.raise_for_status.return_value = None
    r.json.return_value = {"items": items, "next": "url" if has_next else None}
    return r

def test_single_disc_album_offset_needs_no_request(monkeypatch):
    monkeypatch.setenv("SPOTIFY_DEVICE_ID", "test_device")
    with patch.dict('sys.modules', {'utils': MagicMock()}):
        from spotify import SpotifyPlayer
        p = SpotifyPlayer("rfid123", None, "spotify:album:abc")
        with patch.object(p.auth_manager, "get_token", return_value="tok"), \
                patch('spotify.requests.get') as mock_get:
            item = {"track_number": 5, "disc_number": 1}
            assert p._album_offset("abc", item, "spotify:track:x") == 4
            mock_get.assert_not_called()

def test_multi_disc_album_resolves_true_offset(monkeypatch):
    """track_number restarts per disc, so disc 2 track 1 is not offset 0"""
    monkeypatch.setenv("SPOTIFY_DEVICE_ID", "test_device")
    with patch.dict('sys.modules', {'utils': MagicMock()}):
        from spotify import SpotifyPlayer
        p = SpotifyPlayer("rfid123", None, "spotify:album:abc")
        # Disc 1 has 3 tracks; the wanted track is disc 2 track 1 -> index 3.
        tracks = [{"uri": "spotify:track:a"}, {"uri": "spotify:track:b"},
                  {"uri": "spotify:track:c"}, {"uri": "spotify:track:want"}]
        with patch.object(p.auth_manager, "get_token", return_value="tok"), \
                patch('spotify.requests.get', return_value=_page(tracks)):
            item = {"track_number": 1, "disc_number": 2}
            assert p._album_offset("abc", item, "spotify:track:want") == 3

def test_playlist_offset_is_absolute_across_pages(monkeypatch):
    """A match on page 2 must not report its index within that page"""
    monkeypatch.setenv("SPOTIFY_DEVICE_ID", "test_device")
    with patch.dict('sys.modules', {'utils': MagicMock()}):
        from spotify import SpotifyPlayer
        p = SpotifyPlayer("rfid123", None, "spotify:playlist:abc")
        limit = SpotifyPlayer.PAGE_LIMIT
        page1 = _page([{"track": {"uri": "spotify:track:%d" % i}}
                       for i in range(limit)], has_next=True)
        page2 = _page([{"track": {"uri": "spotify:track:other"}},
                       {"track": {"uri": "spotify:track:want"}}])
        with patch.object(p.auth_manager, "get_token", return_value="tok"), \
                patch('spotify.requests.get', side_effect=[page1, page2]) as mock_get:
            # Second entry of the second page -> limit + 1, not 1.
            assert p._get_track_position_in_playlist(
                "abc", "spotify:track:want") == limit + 1
            # /albums/{id}/tracks rejects limit > 50, so never exceed it.
            for call in mock_get.call_args_list:
                assert call.kwargs["params"]["limit"] <= 50

def test_missing_track_falls_back_to_zero(monkeypatch):
    monkeypatch.setenv("SPOTIFY_DEVICE_ID", "test_device")
    with patch.dict('sys.modules', {'utils': MagicMock()}):
        from spotify import SpotifyPlayer
        p = SpotifyPlayer("rfid123", None, "spotify:playlist:abc")
        with patch.object(p.auth_manager, "get_token", return_value="tok"), \
                patch('spotify.requests.get', return_value=_page([])):
            assert p._get_track_position_in_playlist("abc", "nope") == 0

# --- refresh token expiry warning ---
def test_token_age_unknown_without_auth_date(monkeypatch, caplog):
    """A missing date is unknown, never treated as expired"""
    monkeypatch.delenv("SPOTIFY_AUTH_DATE", raising=False)
    import spotify
    assert spotify.check_refresh_token_age() is None

def test_token_age_warns_inside_window(monkeypatch, caplog):
    import datetime
    import spotify
    issued = datetime.date.today() - datetime.timedelta(
        days=spotify.REFRESH_TOKEN_LIFETIME_DAYS - 10)
    monkeypatch.setenv("SPOTIFY_AUTH_DATE", issued.isoformat())

    with caplog.at_level("WARNING"):
        days = spotify.check_refresh_token_age()

    assert days == 10
    assert "expires in about 10 day" in caplog.text
    assert "reauth.py" in caplog.text

def test_token_age_errors_once_past_expiry(monkeypatch, caplog):
    import datetime
    import spotify
    issued = datetime.date.today() - datetime.timedelta(
        days=spotify.REFRESH_TOKEN_LIFETIME_DAYS + 5)
    monkeypatch.setenv("SPOTIFY_AUTH_DATE", issued.isoformat())

    with caplog.at_level("ERROR"):
        days = spotify.check_refresh_token_age()

    assert days == -5
    assert "past its expected lifetime" in caplog.text

def test_token_age_quiet_when_plenty_left(monkeypatch, caplog):
    import datetime
    import spotify
    issued = datetime.date.today() - datetime.timedelta(days=1)
    monkeypatch.setenv("SPOTIFY_AUTH_DATE", issued.isoformat())

    with caplog.at_level("WARNING"):
        days = spotify.check_refresh_token_age()

    assert days == spotify.REFRESH_TOKEN_LIFETIME_DAYS - 1
    assert caplog.text == ""   # no nagging months in advance

def test_token_age_tolerates_garbage_date(monkeypatch):
    import spotify
    monkeypatch.setenv("SPOTIFY_AUTH_DATE", "not-a-date")
    assert spotify.check_refresh_token_age() is None

def test_restart_failure_is_audible(monkeypatch):
    """Rescanning a card onto a dead device must not be silent"""
    monkeypatch.setenv("SPOTIFY_DEVICE_ID", "test_device")
    fake_utils = MagicMock()
    with patch.dict('sys.modules', {'utils': MagicMock()}):
        from spotify import SpotifyPlayer
        player = SpotifyPlayer("rfid123", None, "spotify:album:123")

        with patch.object(player.auth_manager, "get_token", return_value="tok"), \
                patch("spotify.utils", fake_utils), \
                patch('spotify.requests.put',
                      side_effect=requests.RequestException("404")):
            player.restart_playback()

    fake_utils.play_sound.assert_called_once_with("playback_error")

def test_resume_failure_is_audible(monkeypatch):
    monkeypatch.setenv("SPOTIFY_DEVICE_ID", "test_device")
    fake_utils = MagicMock()
    with patch.dict('sys.modules', {'utils': MagicMock()}):
        from spotify import SpotifyPlayer
        player = SpotifyPlayer("rfid123", None, "spotify:album:123")

        with patch.object(player.auth_manager, "get_token", return_value="tok"), \
                patch("spotify.utils", fake_utils), \
                patch('spotify.requests.put',
                      side_effect=requests.RequestException("boom")):
            player.resume_playback()

    fake_utils.play_sound.assert_called_once_with("playback_error")

def test_missing_token_is_transient_unless_credentials_were_rejected(monkeypatch):
    """Distinguishes 'no network yet' from 'Spotify said no'"""
    monkeypatch.setenv("SPOTIFY_DEVICE_ID", "test_device")
    with patch.dict('sys.modules', {'utils': MagicMock()}):
        from spotify import SpotifyPlayer, SpotifyAuthError
        player = SpotifyPlayer("rfid123", None, "spotify:album:123")

        # Never rejected, just could not fetch one: retryable.
        player.auth_manager.rejected_at = 0
        with patch.object(player.auth_manager, "get_token", return_value=None):
            with pytest.raises(SpotifyAuthError) as caught:
                player._get_headers()
        assert caught.value.permanent is False

        # Rejected by Spotify: permanent.
        player.auth_manager.rejected_at = time.time()
        with patch.object(player.auth_manager, "get_token", return_value=None):
            with pytest.raises(SpotifyAuthError) as caught:
                player._get_headers()
        assert caught.value.permanent is True
