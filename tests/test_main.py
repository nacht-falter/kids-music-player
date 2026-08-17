import os
import sys
import threading

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import pytest
from unittest.mock import MagicMock, patch

from main import RFIDMusicPlayer


@pytest.fixture
def app():
    application = RFIDMusicPlayer()
    application.db = MagicMock()
    application.player_lock = threading.Lock()
    application.button_handler = MagicMock()
    return application


def _sounds(mock_utils):
    return [call.args[0] for call in mock_utils.play_sound.call_args_list]


def test_unknown_card_plays_error(app):
    with patch("main.utils") as mock_utils, patch("main.led", None):
        mock_utils.get_music_data.return_value = None
        app.handle_rfid_scan("unknown")

    assert _sounds(mock_utils) == ["error"]


def test_failed_player_creation_is_audible(app):
    """create_player returns None without raising once retries are spent

    That path used to fall through silently: the except branch never ran and
    the finally branch's `if self.player:` was false, so the child heard the
    confirm sound and then nothing at all.
    """
    with patch("main.utils") as mock_utils, patch("main.led", None):
        mock_utils.get_music_data.return_value = {"rfid": "abc"}
        mock_utils.create_player.return_value = None
        app.handle_rfid_scan("abc")

    assert _sounds(mock_utils) == ["confirm", "playback_error"]
    assert app.player is None


def test_raising_player_creation_is_audible_exactly_once(app):
    """The sound moved out of the except branch, so it must not double up"""
    with patch("main.utils") as mock_utils, patch("main.led", None):
        mock_utils.get_music_data.return_value = {"rfid": "abc"}
        mock_utils.create_player.side_effect = RuntimeError("boom")
        app.handle_rfid_scan("abc")

    assert _sounds(mock_utils) == ["confirm", "playback_error"]
    assert mock_utils.play_sound.call_count == 2


def test_successful_scan_plays_and_makes_no_error_sound(app):
    player = MagicMock()
    with patch("main.utils") as mock_utils, patch("main.led", None):
        mock_utils.get_music_data.return_value = {"rfid": "abc"}
        mock_utils.create_player.return_value = player
        app.handle_rfid_scan("abc")

    assert _sounds(mock_utils) == ["confirm"]
    player.play.assert_called_once()
    app.button_handler.set_player.assert_called_once_with(player)


def test_last_played_recorded_even_when_playback_fails(app):
    """Otherwise the play button has nothing to fall back to"""
    with patch("main.utils") as mock_utils, patch("main.led", None):
        mock_utils.get_music_data.return_value = {"rfid": "abc"}
        mock_utils.create_player.return_value = None
        app.handle_rfid_scan("abc")

    mock_utils.save_last_played.assert_called_once_with(app.db, "abc")


def test_rescanning_the_playing_card_toggles_instead(app):
    player = MagicMock()
    player.rfid = "abc"
    app.player = player

    with patch("main.utils") as mock_utils, patch("main.led", None):
        app.handle_rfid_scan("abc")

    assert _sounds(mock_utils) == ["confirm"]
    mock_utils.handle_already_playing.assert_called_once_with(player)
    mock_utils.create_player.assert_not_called()


def test_previous_player_state_saved_before_switching(app):
    """Switching cards must not lose the outgoing album's position"""
    previous = MagicMock()
    previous.rfid = "old"
    app.player = previous

    with patch("main.utils") as mock_utils, patch("main.led", None):
        mock_utils.get_music_data.return_value = {"rfid": "new"}
        mock_utils.create_player.return_value = MagicMock()
        app.handle_rfid_scan("new")

    previous.pause_playback.assert_called_once()
    previous.save_playback_state.assert_called_once()


def test_sync_is_scheduled_not_run_once(app, monkeypatch):
    """sync_db runs once; a new card then waits for a service restart"""
    monkeypatch.setenv("ENABLE_SYNC", "true")
    app.database_url = "/tmp/x.db"

    with patch("main.schedule_sync") as mock_schedule:
        app.setup_sync()

    mock_schedule.assert_called_once()
    assert mock_schedule.call_args.kwargs["interval"] == app.SYNC_INTERVAL


def test_sync_not_scheduled_when_disabled(app, monkeypatch):
    monkeypatch.setenv("ENABLE_SYNC", "false")
    with patch("main.schedule_sync") as mock_schedule:
        app.setup_sync()
    mock_schedule.assert_not_called()
