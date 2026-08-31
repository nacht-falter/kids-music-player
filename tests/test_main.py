import os
import sys
import threading

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import pytest
from unittest.mock import MagicMock, patch

import buttons
from main import RFIDMusicPlayer


@pytest.fixture(params=[buttons.GpioButtonHandler, buttons.IrReceiver],
                ids=["gpio", "ir"])
def app(request):
    application = RFIDMusicPlayer()
    application.db = MagicMock()
    application.player_lock = threading.Lock()
    # Specced against the real handlers: a bare MagicMock invents any attribute
    # it is asked for, which hid a call to a method neither handler has until a
    # card scan crashed the service on the device.
    application.button_handler = MagicMock(spec=request.param)
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
    # The handler is not handed the player; it reads the live one through the
    # get_player callback it was constructed with.
    assert app.player is player


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

# --- idle watchdog -----------------------------------------------------------
#
# These call the loop body directly. An earlier version started the real thread,
# which outlived the patch on main.utils and then invoked the real
# utils.shutdown() - i.e. `sudo shutdown -h now` on the machine running the
# tests. Never spawn the watchdog in a test.

def test_idle_triggers_shutdown(app):
    app.idle_time = 10
    app.last_activity = 0
    with patch("main.utils") as mock_utils:
        assert app.check_idle(now=999) is True
        mock_utils.shutdown.assert_called_once()


def test_recent_activity_does_not_shut_down(app):
    app.idle_time = 10
    app.last_activity = 995
    with patch("main.utils") as mock_utils:
        assert app.check_idle(now=999) is False
        mock_utils.shutdown.assert_not_called()


def test_playback_counts_as_activity(app):
    """A story longer than idle_time used to be cut off mid-way"""
    player = MagicMock()
    player.playing = True
    app.player = player
    app.last_activity = 0

    app.record_playback_activity()

    player.refresh_playback_state.assert_called_once()
    assert app.last_activity > 0, "playback did not reset the idle timer"


def test_paused_playback_is_not_activity(app):
    player = MagicMock()
    player.playing = False
    app.player = player
    app.last_activity = 0

    app.record_playback_activity()

    assert app.last_activity == 0, "paused playback kept the device awake"


def test_streaming_without_a_card_counts_as_activity(app):
    """Nothing has been scanned since boot, so there is no player to ask"""
    app.player = None
    app.last_activity = 0

    with patch("main.spotify.device_is_playing", return_value=True) as probe:
        app.record_playback_activity()

    probe.assert_called_once()
    assert app.last_activity > 0


def test_idle_without_a_card_is_not_activity(app):
    app.player = None
    app.last_activity = 0

    with patch("main.spotify.device_is_playing", return_value=False):
        app.record_playback_activity()

    assert app.last_activity == 0


def test_startup_warns_when_the_shutdown_dry_run_is_left_on(monkeypatch, caplog, tmp_path):
    """A box that cannot power itself off drains the bank all night (item 34),
    and the symptom looks nothing like a leftover test flag - so say it every
    startup. It has to come after logging is configured; in verify_env_file,
    which runs first, the warning went nowhere.
    """
    for key, value in {
        "SPOTIFY_USERCREDS": "x", "SPOTIFY_REFRESH_TOKEN": "x",
        "SPOTIFY_DEVICE_ID": "x", "DATABASE_URL": "x", "RFID_READER": "x",
        "APP_NAME": "dryruntest", "SHUTDOWN_DRY_RUN": "true",
    }.items():
        monkeypatch.setenv(key, value)

    application = RFIDMusicPlayer()
    monkeypatch.chdir(tmp_path)
    with patch("main.load_dotenv"), \
            patch("main.os.path.dirname", return_value=str(tmp_path)), \
            caplog.at_level("WARNING"):
        application.initialize()

    assert "will NOT power off" in caplog.text
