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

# --- log volume ---
def test_http_failures_log_without_a_traceback(monkeypatch, caplog):
    """Ten retries used to mean ten identical raise_for_status stacks"""
    monkeypatch.setenv("SPOTIFY_DEVICE_ID", "test_device")
    with patch.dict('sys.modules', {'utils': MagicMock()}):
        from spotify import SpotifyPlayer
        player = SpotifyPlayer("rfid123", None, "spotify:album:123")

        with caplog.at_level("ERROR"):
            player.handle_exception(
                "Transfer playback failed",
                requests.HTTPError("404 Client Error: Not Found for url: x"))

    assert len(caplog.records) == 1
    # logging passes a falsy exc_info straight through, so this is False rather
    # than None; what matters is that no traceback is attached.
    assert not caplog.records[0].exc_info
    # The useful detail still survives.
    assert "404" in caplog.text and "Transfer playback failed" in caplog.text

def test_unexpected_failures_keep_their_traceback(monkeypatch, caplog):
    """The AttributeError that killed the watchdog was only readable as a stack"""
    monkeypatch.setenv("SPOTIFY_DEVICE_ID", "test_device")
    with patch.dict('sys.modules', {'utils': MagicMock()}):
        from spotify import SpotifyPlayer
        player = SpotifyPlayer("rfid123", None, "spotify:album:123")

        with caplog.at_level("ERROR"):
            try:
                (None).get("uri")
            except AttributeError as e:
                player.handle_exception("Failed to resolve track position", e)

    assert len(caplog.records) == 1
    assert caplog.records[0].exc_info is not None

# --- playback check independent of any player ---
def _player_response(device_id, is_playing, status=200):
    r = MagicMock()
    r.status_code = status
    r.raise_for_status.return_value = None
    r.json.return_value = {"device": {"id": device_id}, "is_playing": is_playing}
    return r

def test_device_is_playing_true_for_our_device(monkeypatch):
    monkeypatch.setenv("SPOTIFY_DEVICE_ID", "ours")
    import spotify
    with patch.object(spotify, "get_auth_manager") as am, \
            patch("spotify.requests.get", return_value=_player_response("ours", True)):
        am.return_value.get_token.return_value = "tok"
        assert spotify.device_is_playing() is True

def test_device_is_playing_false_when_another_device_plays(monkeypatch):
    """A phone playing to itself must not keep our speaker awake"""
    monkeypatch.setenv("SPOTIFY_DEVICE_ID", "ours")
    import spotify
    with patch.object(spotify, "get_auth_manager") as am, \
            patch("spotify.requests.get", return_value=_player_response("phone", True)):
        am.return_value.get_token.return_value = "tok"
        assert spotify.device_is_playing() is False

def test_device_is_playing_false_when_paused(monkeypatch):
    monkeypatch.setenv("SPOTIFY_DEVICE_ID", "ours")
    import spotify
    with patch.object(spotify, "get_auth_manager") as am, \
            patch("spotify.requests.get", return_value=_player_response("ours", False)):
        am.return_value.get_token.return_value = "tok"
        assert spotify.device_is_playing() is False

def test_device_is_playing_fails_closed(monkeypatch):
    """Any doubt must not pin the device awake - it runs on a battery"""
    monkeypatch.setenv("SPOTIFY_DEVICE_ID", "ours")
    import spotify

    # 204: nothing playing anywhere
    with patch.object(spotify, "get_auth_manager") as am, \
            patch("spotify.requests.get", return_value=MagicMock(status_code=204)):
        am.return_value.get_token.return_value = "tok"
        assert spotify.device_is_playing() is False

    # network error
    with patch.object(spotify, "get_auth_manager") as am, \
            patch("spotify.requests.get",
                  side_effect=requests.RequestException("down")):
        am.return_value.get_token.return_value = "tok"
        assert spotify.device_is_playing() is False

    # no token
    with patch.object(spotify, "get_auth_manager") as am:
        am.return_value.get_token.return_value = None
        assert spotify.device_is_playing() is False

    # no device configured
    monkeypatch.delenv("SPOTIFY_DEVICE_ID", raising=False)
    assert spotify.device_is_playing() is False

# --- previous-track behaviour ------------------------------------------------
def test_previous_restarts_the_track_when_past_the_threshold(monkeypatch):
    """What a CD player does, and what the bash version did"""
    monkeypatch.setenv("SPOTIFY_DEVICE_ID", "test_device")
    with patch.dict('sys.modules', {'utils': MagicMock()}):
        from spotify import SpotifyPlayer
        player = SpotifyPlayer("rfid123", None, "spotify:album:123")

        status = _playback_status("test_device", "spotify:album:123")
        status.json.return_value["progress_ms"] = 45000
        status.json.return_value["item"] = {"uri": "spotify:track:x",
                                            "track_number": 2}
        with patch.object(player.auth_manager, "get_token", return_value="tok"), \
                patch('spotify.requests.get', return_value=status), \
                patch('spotify.requests.put') as mock_put, \
                patch('spotify.requests.post') as mock_post:
            player.previous_track()

        mock_post.assert_not_called()          # did not skip back
        assert "seek" in mock_put.call_args.args[0]
        assert "position_ms=0" in mock_put.call_args.args[0]

def test_previous_skips_back_near_the_start(monkeypatch):
    monkeypatch.setenv("SPOTIFY_DEVICE_ID", "test_device")
    with patch.dict('sys.modules', {'utils': MagicMock()}):
        from spotify import SpotifyPlayer
        player = SpotifyPlayer("rfid123", None, "spotify:album:123")

        status = _playback_status("test_device", "spotify:album:123")
        status.json.return_value["progress_ms"] = 1200      # under 3s
        status.json.return_value["item"] = {"uri": "spotify:track:x",
                                            "track_number": 2}
        with patch.object(player.auth_manager, "get_token", return_value="tok"), \
                patch('spotify.requests.get', return_value=status), \
                patch('spotify.requests.post') as mock_post:
            player.previous_track()

        mock_post.assert_called_once()
        assert "previous" in mock_post.call_args.args[0]


# --- clock-jump resilience ---------------------------------------------------

class TestImpossiblePositions:
    """The Pi has no RTC, so its clock jumps when NTP finally syncs

    spotifyd derives progress from the wall clock. A jump while it is running
    made /me/player report progress_ms nearly twenty minutes negative, and the
    refresh loop wrote every reading straight into the database - overwriting a
    good saved position with one no playback can be at.
    """

    ITEM = {"duration_ms": 183794, "track_number": 2, "uri": "spotify:track:t"}

    def _player(self, monkeypatch):
        monkeypatch.setenv("SPOTIFY_DEVICE_ID", "test_device")
        with patch.dict('sys.modules', {'utils': MagicMock()}):
            from spotify import SpotifyPlayer
            return SpotifyPlayer("rfid123", None, "spotify:album:123")

    def test_negative_position_is_rejected(self, monkeypatch):
        """The value the device actually reported"""
        player = self._player(monkeypatch)
        assert player._position_is_impossible(-1073374, self.ITEM) is True

    def test_position_past_the_end_is_rejected(self, monkeypatch):
        player = self._player(monkeypatch)
        assert player._position_is_impossible(999999, self.ITEM) is True

    def test_position_just_past_the_end_is_allowed(self, monkeypatch):
        """A tick can land after the end just before the track changes"""
        player = self._player(monkeypatch)
        assert player._position_is_impossible(183794 + 1000, self.ITEM) is False

    def test_normal_position_is_allowed(self, monkeypatch):
        player = self._player(monkeypatch)
        assert player._position_is_impossible(13038, self.ITEM) is False

    def test_missing_duration_still_allows_a_sane_position(self, monkeypatch):
        player = self._player(monkeypatch)
        assert player._position_is_impossible(13038, {}) is False

    def test_snapshot_keeps_the_last_good_position(self, monkeypatch):
        """The saved place must survive a clock jump, not be overwritten"""
        player = self._player(monkeypatch)
        player.playback_state = {"offset": {"position": 1},
                                 "position_ms": 13038}

        state = player._state_from_playback({
            "item": self.ITEM,
            "context": {"uri": "spotify:album:123"},
            "progress_ms": -1073374,
        })

        assert state == {"offset": {"position": 1}, "position_ms": 13038}

    def test_snapshot_takes_a_sane_position(self, monkeypatch):
        player = self._player(monkeypatch)
        player.playback_state = {"offset": {"position": 0}, "position_ms": 0}

        state = player._state_from_playback({
            "item": self.ITEM,
            "context": {"uri": "spotify:album:123"},
            "progress_ms": 42000,
        })

        assert state["position_ms"] == 42000
        assert state["offset"]["position"] == 1   # track_number 2, zero-based

    def test_save_persists_the_last_good_position(self, monkeypatch):
        """save_playback_state must not write the garbage either"""
        player = self._player(monkeypatch)
        player.playback_state = {"offset": {"position": 1},
                                 "position_ms": 13038}
        playback = {
            "device": {"id": "test_device"},
            "context": {"uri": "spotify:album:123"},
            "item": self.ITEM,
            "is_playing": True,
            "progress_ms": -1073374,
        }

        with patch.object(player, "check_playback_status",
                          return_value=playback), \
                patch.object(player, "_persist_state",
                             return_value=True) as persist:
            player.save_playback_state()

        persist.assert_called_once_with({"offset": {"position": 1},
                                         "position_ms": 13038})


class TestPositionDiagnostics:
    """The warning has to carry enough evidence to diagnose the next sighting

    This has fired twice in the wild and reproduced on the bench zero times:
    neither a stale clock at boot nor a mid-session step of the same magnitude
    corrupted progress_ms on the deployed spotifyd. So the log line is the
    investigation, and the discrepancy against the monotonic clock is the part
    that actually distinguishes the theories.
    """

    ITEM = {"id": "track1", "duration_ms": 183794, "uri": "spotify:track:t"}

    def _player(self, monkeypatch):
        monkeypatch.setenv("SPOTIFY_DEVICE_ID", "test_device")
        with patch.dict('sys.modules', {'utils': MagicMock()}):
            from spotify import SpotifyPlayer
            return SpotifyPlayer("rfid123", None, "spotify:album:123")

    def _playback(self, progress_ms):
        return {"item": self.ITEM, "progress_ms": progress_ms,
                "is_playing": True, "timestamp": 1787147489761,
                "context": {"uri": "spotify:album:123"},
                "device": {"id": "test_device"}}

    def test_discrepancy_is_measured_against_the_monotonic_clock(
            self, monkeypatch):
        """The one measurement librespot's clock cannot distort"""
        player = self._player(monkeypatch)

        with patch("spotify.time.monotonic", return_value=1000.0):
            player._note_good_position(self._playback(30000), 30000)

        # 10s of real time later, the device claims to be 15 minutes behind.
        with patch("spotify.time.monotonic", return_value=1010.0):
            line = player._position_diagnostics(self._playback(-880706),
                                                -880706)

        assert "monotonic_elapsed_ms=10000" in line
        assert "expected_ms=40000" in line
        assert "discrepancy_ms=-920706" in line

    def test_diagnostics_survive_having_no_anchor_yet(self, monkeypatch):
        """The first reading of a session can be the bad one"""
        player = self._player(monkeypatch)

        line = player._position_diagnostics(self._playback(-880706), -880706)

        assert "last_good_ms=none" in line
        assert "discrepancy_ms" not in line

    def test_diagnostics_include_the_context_for_the_theories_on_the_table(
            self, monkeypatch):
        """uptime distinguishes "just booted" from "hours in"; api_timestamp
        is what Spotify itself thinks; system_clock catches a stepped clock."""
        player = self._player(monkeypatch)

        line = player._position_diagnostics(self._playback(-880706), -880706)

        for field in ("reported_ms=-880706", "api_timestamp=1787147489761",
                      "system_clock=", "monotonic=", "uptime_s=",
                      "track=track1", "duration_ms=183794", "is_playing=True"):
            assert field in line, f"{field} missing from: {line}"

    def test_an_accepted_reading_becomes_the_anchor(self, monkeypatch):
        player = self._player(monkeypatch)
        player.owns_playback = lambda playback: True

        player._state_from_playback(self._playback(13038))

        assert player.last_good_position is not None
        assert player.last_good_position[0] == 13038

    def test_a_rejected_reading_does_not_become_the_anchor(self, monkeypatch):
        """Otherwise the anchor is poisoned and the discrepancy reads zero"""
        player = self._player(monkeypatch)
        player.owns_playback = lambda playback: True
        with patch("spotify.time.monotonic", return_value=1000.0):
            player._note_good_position(self._playback(30000), 30000)

        player._state_from_playback(self._playback(-880706))

        assert player.last_good_position == (30000, 1000.0, "track1")


class TestPositionDriftAgainstMonotonic:
    """A clock step smaller than the track is invisible to the bounds check

    Every corruption caught in the wild so far has been negative, which the
    bounds check already rejects. The same step lands inside [0, duration]
    whenever it is smaller than the track, and then it reads as an entirely
    ordinary position - the child simply resumes somewhere they never were.
    time.monotonic() is the one reference a stepped clock cannot move.
    """

    ITEM = {"id": "track1", "duration_ms": 183794, "uri": "spotify:track:t"}
    OTHER = {"id": "track2", "duration_ms": 200000, "uri": "spotify:track:u"}

    def _player(self, monkeypatch):
        monkeypatch.setenv("SPOTIFY_DEVICE_ID", "test_device")
        with patch.dict('sys.modules', {'utils': MagicMock()}):
            from spotify import SpotifyPlayer
            player = SpotifyPlayer("rfid123", None, "spotify:album:123")
        player.owns_playback = lambda playback: True
        player.playback_state = {"offset": {"position": 0}, "position_ms": 30000}
        return player

    def _playback(self, progress_ms, item=None, is_playing=True):
        return {"item": item if item is not None else self.ITEM,
                "progress_ms": progress_ms, "is_playing": is_playing,
                "timestamp": 1787147489761,
                "context": {"uri": "spotify:album:123"},
                "device": {"id": "test_device"}}

    def _anchor(self, player, position_ms, at=1000.0):
        with patch("spotify.time.monotonic", return_value=at):
            player._note_good_position(self._playback(position_ms), position_ms)

    def test_a_position_inside_the_track_but_wrong_is_rejected(
            self, monkeypatch):
        """The whole point: bounds cannot see this, elapsed time can"""
        player = self._player(monkeypatch)
        self._anchor(player, 30000)

        # 10s later the device reports 29s earlier than it should - still a
        # perfectly legal position inside a 183s track.
        with patch("spotify.time.monotonic", return_value=1010.0):
            state = player._state_from_playback(self._playback(11000))

        assert player._position_is_impossible(11000, self.ITEM) is False
        assert state["position_ms"] == 30000

    def test_a_position_matching_elapsed_time_is_accepted(self, monkeypatch):
        player = self._player(monkeypatch)
        self._anchor(player, 30000)

        with patch("spotify.time.monotonic", return_value=1010.0):
            state = player._state_from_playback(self._playback(40000))

        assert state["position_ms"] == 40000

    def test_ordinary_round_trip_lag_is_not_drift(self, monkeypatch):
        """Measured live at ~520ms; the tolerance has to clear it easily"""
        player = self._player(monkeypatch)
        self._anchor(player, 30000)

        with patch("spotify.time.monotonic", return_value=1010.0):
            state = player._state_from_playback(self._playback(39400))

        assert state["position_ms"] == 39400

    def test_a_track_change_stands_the_check_down(self, monkeypatch):
        """A new track legitimately resets the position to near zero"""
        player = self._player(monkeypatch)
        self._anchor(player, 170000)

        with patch("spotify.time.monotonic", return_value=1010.0):
            state = player._state_from_playback(
                self._playback(2000, item=self.OTHER))

        assert state["position_ms"] == 2000

    def test_a_paused_reading_stands_the_check_down(self, monkeypatch):
        """Position stands still while monotonic time keeps moving"""
        player = self._player(monkeypatch)
        self._anchor(player, 30000)

        with patch("spotify.time.monotonic", return_value=1100.0):
            state = player._state_from_playback(
                self._playback(30000, is_playing=False))

        assert state["position_ms"] == 30000

    def test_a_pause_drops_the_anchor(self, monkeypatch):
        """Else the pause itself reads as drift once playback resumes"""
        player = self._player(monkeypatch)
        self._anchor(player, 30000)
        assert player.last_good_position is not None

        player._note_good_position(
            self._playback(30000, is_playing=False), 30000)

        assert player.last_good_position is None

    def test_a_skip_drops_the_anchor(self, monkeypatch):
        """Otherwise the jump the skip caused reads as a clock step"""
        player = self._player(monkeypatch)
        player.ensure_owns_playback = lambda action: True
        self._anchor(player, 30000)

        with patch("spotify.requests.post") as post:
            post.return_value = MagicMock(raise_for_status=lambda: None)
            player.next_track()

        assert player.last_good_position is None

    def test_no_anchor_means_no_opinion(self, monkeypatch):
        """The first reading of a session has nothing to be checked against"""
        player = self._player(monkeypatch)
        player.last_good_position = None

        state = player._state_from_playback(self._playback(11000))

        assert state["position_ms"] == 11000

    def test_play_anchors_on_the_position_it_asked_for(self, monkeypatch):
        """A session can begin already poisoned, so the first reading back is
        not evidence of anything - observed in the wild, where impossible
        readings continued through three consecutive fresh play() calls."""
        player = self._player(monkeypatch)
        player.playback_state = {"offset": {"position": 0},
                                 "position_ms": 45000}

        with patch("spotify.requests.put") as put, \
                patch("spotify.time.monotonic", return_value=2000.0):
            put.return_value = MagicMock(raise_for_status=lambda: None)
            player.play()

        assert player.last_good_position == (45000, 2000.0, None)

        # The device answers 40s adrift, comfortably inside the track. The
        # requested position is what catches it.
        with patch("spotify.time.monotonic", return_value=2002.0):
            state = player._state_from_playback(self._playback(7000))

        assert state["position_ms"] == 45000

    def test_an_anchor_from_a_request_accepts_whatever_track_replies(
            self, monkeypatch):
        """play() asks for an offset, not a track id, so the first reading
        defines which track the anchor belongs to."""
        player = self._player(monkeypatch)
        player.last_good_position = (45000, 2000.0, None)

        with patch("spotify.time.monotonic", return_value=2002.0):
            state = player._state_from_playback(
                self._playback(47000, item=self.OTHER))

        assert state["position_ms"] == 47000
        assert player.last_good_position == (47000, 2002.0, "track2")


class TestSeriesCapabilityFlag:
    """utils.handle_already_playing branches on is_series, so the real classes
    must carry it. Mocks set it by hand, which hid a missing flag entirely:
    deleting it from SpotifySeriesPlayer left the whole suite green."""

    def test_a_plain_player_is_not_a_series(self, monkeypatch):
        monkeypatch.setenv("SPOTIFY_DEVICE_ID", "test_device")
        with patch.dict('sys.modules', {'utils': MagicMock()}):
            from spotify import SpotifyPlayer
        assert SpotifyPlayer.is_series is False

    def test_a_series_player_is_a_series(self, monkeypatch):
        monkeypatch.setenv("SPOTIFY_DEVICE_ID", "test_device")
        with patch.dict('sys.modules', {'utils': MagicMock()}):
            from spotify import SpotifySeriesPlayer
        assert SpotifySeriesPlayer.is_series is True



class TestResumeNeedsAContextWeLoaded:
    """A bare resume is only correct once we know what is loaded there

    Observed on toem2 on 2026-08-26: press play after boot, the session is
    transferred, a bare resume is sent 450ms later, Spotify answers 204 - and
    nothing plays, because spotifyd has been handed the session and not yet
    attached to the context. playback_started is what tells the two cases
    apart, and until now nothing read it.
    """

    def player(self, monkeypatch, playback_started):
        monkeypatch.setenv("SPOTIFY_DEVICE_ID", "test_device")
        with patch.dict('sys.modules', {'utils': MagicMock()}):
            from spotify import SpotifyPlayer
            player = SpotifyPlayer("rfid123", None, "spotify:album:123")
        player.playback_started = playback_started
        return player

    def playback(self, is_playing=False, context="spotify:album:123",
                 device="test_device"):
        return {
            "device": {"id": device},
            "context": {"uri": context},
            "is_playing": is_playing,
            "item": {"duration_ms": 100000},
            "progress_ms": 42000,
        }

    def toggle(self, player, playback):
        with patch.object(player, "check_playback_status",
                          side_effect=lambda: self._answer(player, playback)), \
                patch.object(player, "play") as played, \
                patch.object(player, "resume_playback") as resumed, \
                patch.object(player, "pause_playback") as paused:
            player.toggle_playback()
        return played, resumed, paused

    def _answer(self, player, playback):
        if playback is None:
            player.playing = False
            return None
        player.playing = (playback["device"]["id"] == player.device_id
                          and playback["is_playing"])
        return playback

    def test_fresh_player_names_the_context_instead_of_resuming(
            self, monkeypatch):
        """The bug. Nothing here loaded that context, so do not assume it"""
        player = self.player(monkeypatch, playback_started=False)
        played, resumed, _ = self.toggle(player, self.playback())
        played.assert_called_once_with()
        resumed.assert_not_called()

    def test_once_we_have_driven_it_a_bare_resume_is_correct(self, monkeypatch):
        player = self.player(monkeypatch, playback_started=True)
        played, resumed, _ = self.toggle(player, self.playback())
        resumed.assert_called_once_with()
        played.assert_not_called()

    def test_still_pauses_what_is_actually_playing(self, monkeypatch):
        """d154474's case: spotifyd outlived us and is still streaming"""
        player = self.player(monkeypatch, playback_started=False)
        played, resumed, paused = self.toggle(
            player, self.playback(is_playing=True))
        paused.assert_called_once_with()
        played.assert_not_called()
        resumed.assert_not_called()

    def test_a_rebuilt_player_resumes_where_the_story_is(self, monkeypatch):
        """play() must not seek to the stored position after a restart

        check_playback_status() refreshes playback_state from the live payload
        whenever the playback is ours, so naming the context lands on the live
        position - which is what made switching away from resume safe.
        """
        player = self.player(monkeypatch, playback_started=False)
        player.playback_state = {"offset": {"position": 0}, "position_ms": 0}
        with patch.object(player.auth_manager, "get_token", return_value="tok"), \
                patch("spotify.utils", MagicMock()), \
                patch("spotify.requests.get") as got, \
                patch("spotify.requests.put") as put:
            got.return_value = MagicMock(
                status_code=200, raise_for_status=lambda: None,
                json=lambda: self.playback())
            put.return_value = MagicMock(raise_for_status=lambda: None)
            player.toggle_playback()
        assert put.call_args.kwargs["json"]["position_ms"] == 42000

    def test_foreign_session_is_still_never_adopted(self, monkeypatch):
        player = self.player(monkeypatch, playback_started=True)
        played, resumed, _ = self.toggle(
            player, self.playback(context="spotify:album:someone_else"))
        played.assert_called_once_with()
        resumed.assert_not_called()

    def test_nothing_playing_anywhere_names_the_context(self, monkeypatch):
        player = self.player(monkeypatch, playback_started=True)
        played, resumed, _ = self.toggle(player, None)
        played.assert_called_once_with()
        resumed.assert_not_called()
