import os
import sys
import threading

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import pytest
from unittest.mock import MagicMock, patch


@pytest.fixture
def handler():
    """The shared action handler, independent of GPIO or IR

    Built directly rather than through GpioButtonHandler, which needs gpiozero
    and is absent on the IR device.
    """
    import buttons
    with patch("buttons.subprocess.run", side_effect=OSError("no amixer")):
        h = buttons.PlayerActionHandler(
            get_player=MagicMock(return_value=None),
            set_player=MagicMock(),
            database_url=":memory:",
            player_lock=threading.Lock(),
            reset_last_activity=MagicMock(),
        )
    h.SHUTDOWN_CONFIRM_TIMEOUT = 0.05  # keep the tests quick
    yield h
    if h.confirm_timer:
        h.confirm_timer.cancel()


def test_single_press_only_confirms(handler):
    with patch("buttons.utils") as mock_utils:
        handler.handle_action("shutdown")
        mock_utils.play_sound.assert_called_once_with("confirm_shutdown")
        mock_utils.shutdown.assert_not_called()


def test_second_press_shuts_down(handler):
    with patch("buttons.utils") as mock_utils:
        handler.handle_action("shutdown")
        handler.handle_action("shutdown")
        mock_utils.shutdown.assert_called_once()


def test_confirmation_expires(handler):
    """An accidental press must not leave the device armed indefinitely"""
    with patch("buttons.utils") as mock_utils:
        handler.handle_action("shutdown")

        # Wait out the confirmation window.
        expired = threading.Event()
        expired.wait(handler.SHUTDOWN_CONFIRM_TIMEOUT + 0.15)

        handler.handle_action("shutdown")
        # The second press re-confirms rather than shutting down.
        mock_utils.shutdown.assert_not_called()
        assert mock_utils.play_sound.call_count == 2


def test_third_rapid_press_still_shuts_down(handler):
    """`== 2` left a rapid triple-press matching neither branch"""
    with patch("buttons.utils") as mock_utils:
        handler.handle_action("shutdown")
        handler.handle_action("shutdown")
        mock_utils.shutdown.reset_mock()

        # Counter was cleared, so this starts a fresh confirmation.
        handler.handle_action("shutdown")
        mock_utils.shutdown.assert_not_called()
        handler.handle_action("shutdown")
        mock_utils.shutdown.assert_called_once()


def test_other_button_cancels_the_arming(handler):
    with patch("buttons.utils") as mock_utils:
        handler.handle_action("shutdown")
        handler.handle_action("next_track")
        handler.handle_action("shutdown")
        mock_utils.shutdown.assert_not_called()


# --- recovering a player after the controller restarted ----------------------

class TestTogglingWithNoPlayer:
    """spotifyd outlives this process, so audio can be playing with no player

    Restarting the service (a crash, a deploy, a manual restart) drops the
    in-memory player, but the Spotify Connect session in spotifyd keeps
    streaming. The next button press has to cope with a speaker that is
    already playing.
    """

    def _press_with_last_played(self, handler, player):
        with patch("buttons.utils") as mock_utils, patch("buttons.led", None):
            mock_utils.get_last_played_rfid.return_value = "abc"
            mock_utils.get_music_data.return_value = {"rfid": "abc"}
            mock_utils.create_player.return_value = player
            handler.handle_action("toggle_playback")
        return mock_utils

    def test_does_not_seek_when_the_speaker_is_already_playing(self, handler):
        """A pause press must not become a jump to the stored position

        play() PUTs context_uri plus the stored position unconditionally, so
        using it here restarted the story somewhere the child did not expect.
        """
        player = MagicMock()
        self._press_with_last_played(handler, player)

        player.play.assert_not_called()
        player.toggle_playback.assert_called_once()

    def test_the_new_player_is_kept(self, handler):
        player = MagicMock()
        self._press_with_last_played(handler, player)
        handler.set_player.assert_called_once_with(player)

    def test_missing_last_played_is_audible(self, handler):
        with patch("buttons.utils") as mock_utils, patch("buttons.led", None):
            mock_utils.get_music_data.return_value = None
            handler.handle_action("toggle_playback")

        mock_utils.create_player.assert_not_called()
        assert "error" in [c.args[0]
                           for c in mock_utils.play_sound.call_args_list]

    def test_a_second_press_toggles_rather_than_restarting(self, handler):
        """playback_started gates toggle-vs-start on the following press

        It used to be set only by play(); adopting playback left it False, so
        the next press fell through to play() and seeked after all.
        """
        import spotify
        player = spotify.SpotifyPlayer.__new__(spotify.SpotifyPlayer)
        player.playing = True
        player.playback_started = False
        player.device_id = "dev1"

        with patch.object(player, "_device_url", return_value="http://x"), \
                patch.object(player, "_get_headers", return_value={}), \
                patch("spotify.requests.put", return_value=MagicMock(
                    raise_for_status=MagicMock(return_value=None))):
            player.pause_playback()

        assert player.playback_started is True


# --- volume (IR only; toem2 has a hardware pot) ------------------------------

def _scontrols(*names):
    listing = "".join("Simple mixer control '%s',0\n" % n for n in names)
    return MagicMock(stdout=listing)


class TestMixerDetection:
    """The control name varies by device and by OS release

    The bash version this replaces hardcoded "Headphone", which no longer
    exists on that hardware - its volume had silently stopped working.
    """

    def _handler(self):
        import buttons
        return buttons.PlayerActionHandler.__new__(buttons.PlayerActionHandler)

    def test_prefers_the_first_known_candidate(self):
        with patch("buttons.subprocess.run", return_value=_scontrols("Master", "PCM")):
            assert self._handler()._detect_mixer_control() == "PCM"

    def test_env_override_wins(self, monkeypatch):
        monkeypatch.setenv("MIXER_CONTROL", "Master")
        with patch("buttons.subprocess.run", return_value=_scontrols("Master", "PCM")):
            assert self._handler()._detect_mixer_control() == "Master"

    def test_unavailable_override_falls_back_and_warns(self, monkeypatch, caplog):
        monkeypatch.setenv("MIXER_CONTROL", "Nonexistent")
        with patch("buttons.subprocess.run", return_value=_scontrols("PCM")), \
                caplog.at_level("WARNING"):
            assert self._handler()._detect_mixer_control() == "PCM"
        assert "Nonexistent" in caplog.text

    def test_unknown_control_still_used(self, monkeypatch):
        """Names vary enough that anything beats giving up"""
        monkeypatch.delenv("MIXER_CONTROL", raising=False)
        with patch("buttons.subprocess.run", return_value=_scontrols("SomeDAC")):
            assert self._handler()._detect_mixer_control() == "SomeDAC"

    def test_no_controls_returns_none(self, monkeypatch):
        monkeypatch.delenv("MIXER_CONTROL", raising=False)
        with patch("buttons.subprocess.run", return_value=_scontrols()):
            assert self._handler()._detect_mixer_control() is None

    def test_amixer_missing_returns_none(self, monkeypatch):
        monkeypatch.delenv("MIXER_CONTROL", raising=False)
        with patch("buttons.subprocess.run", side_effect=OSError("no amixer")):
            assert self._handler()._detect_mixer_control() is None


class TestVolume:
    def _handler(self, current):
        import buttons
        h = buttons.PlayerActionHandler.__new__(buttons.PlayerActionHandler)
        h.mixer_control = "PCM"
        h._current_volume = lambda: current
        return h

    def test_step_up(self):
        h = self._handler(50)
        with patch("buttons.subprocess.run") as run, patch("buttons.utils"):
            h._set_volume(+10)
        assert "60%" in run.call_args.args[0]

    def test_clamped_to_max(self):
        """Above the cap it distorts, so stop there rather than at 100"""
        import buttons
        h = self._handler(buttons.PlayerActionHandler.VOLUME_MAX - 5)
        with patch("buttons.subprocess.run") as run, patch("buttons.utils"):
            h._set_volume(+10)
        assert f"{buttons.PlayerActionHandler.VOLUME_MAX}%" in run.call_args.args[0]

    def test_at_the_end_stop_says_so(self):
        import buttons
        h = self._handler(buttons.PlayerActionHandler.VOLUME_MAX)
        with patch("buttons.subprocess.run") as run, patch("buttons.utils") as u:
            h._set_volume(+10)
        run.assert_not_called()
        u.play_sound.assert_called_once_with("error")

    def test_unreadable_volume_is_audible(self):
        h = self._handler(None)
        with patch("buttons.subprocess.run") as run, patch("buttons.utils") as u:
            h._set_volume(+10)
        run.assert_not_called()
        u.play_sound.assert_called_once_with("error")


# --- IR socket handling ------------------------------------------------------

def _ir(monkeypatch, tmp_path):
    """An IrReceiver with a real socket path and a stubbed action handler"""
    import buttons
    sock_path = tmp_path / "lircd"
    sock_path.touch()
    with patch("buttons.subprocess.run", side_effect=OSError("no amixer")):
        r = buttons.IrReceiver(
            get_player=MagicMock(), set_player=MagicMock(),
            database_url=":memory:", player_lock=threading.Lock(),
            reset_last_activity=MagicMock(), socket_path=str(sock_path))
    r.action_handler = MagicMock()
    return r


class _FakeSocketFile:
    """Yields lines, then behaves as a closed socket: "" forever"""
    def __init__(self, lines):
        self.lines = list(lines)
        self.reads_after_close = 0

    def readline(self):
        if self.lines:
            return self.lines.pop(0)
        self.reads_after_close += 1
        if self.reads_after_close > 1000:
            raise AssertionError("busy loop: kept reading a closed socket")
        return ""


def test_closed_socket_does_not_spin(monkeypatch, tmp_path):
    """readline() returns "" forever once closed; continuing pegs the CPU"""
    r = _ir(monkeypatch, tmp_path)
    r._running = True
    f = _FakeSocketFile(["0001 00 KEY_NEXT devinput\n"])

    r._consume(f)   # must return, not spin

    assert f.reads_after_close == 1
    r.action_handler.handle_action.assert_called_once_with("next_track")


def test_held_key_fires_once_but_volume_repeats(monkeypatch, tmp_path):
    r = _ir(monkeypatch, tmp_path)
    r._running = True
    r._consume(_FakeSocketFile([
        "0001 00 KEY_NEXT devinput\n",
        "0001 01 KEY_NEXT devinput\n",
        "0002 00 KEY_VOLUMEUP devinput\n",
        "0002 01 KEY_VOLUMEUP devinput\n",
        "0003 00 KEY_NOTAKEY devinput\n",
    ]))
    actions = [c.args[0] for c in r.action_handler.handle_action.call_args_list]
    assert actions == ["next_track", "volume_up", "volume_up"]


def test_a_failing_action_does_not_end_ir_input(monkeypatch, tmp_path):
    """One bad action must not cost the remote for the rest of the uptime"""
    r = _ir(monkeypatch, tmp_path)
    r._running = True
    r.action_handler.handle_action.side_effect = [RuntimeError("boom"), None]

    r._consume(_FakeSocketFile([
        "0001 00 KEY_NEXT devinput\n",
        "0002 00 KEY_PLAY devinput\n",
    ]))

    assert r.action_handler.handle_action.call_count == 2


def test_socket_failure_reconnects(monkeypatch, tmp_path):
    """A dropped socket must not silently end IR input"""
    import buttons
    r = _ir(monkeypatch, tmp_path)
    r.RECONNECT_DELAY = 0.01
    r._running = True
    attempts = []

    def failing_socket(*a, **k):
        attempts.append(1)
        if len(attempts) >= 3:
            r._running = False          # let the loop end so the test finishes
        raise OSError("lircd is away")

    with patch("buttons.socket.socket", side_effect=failing_socket):
        r._read_loop()

    assert len(attempts) >= 3, "gave up after the first failure"


def test_start_refuses_without_a_socket(monkeypatch, tmp_path):
    import buttons
    with patch("buttons.subprocess.run", side_effect=OSError("no amixer")):
        r = buttons.IrReceiver(
            get_player=MagicMock(), set_player=MagicMock(),
            database_url=":memory:", player_lock=threading.Lock(),
            reset_last_activity=MagicMock(),
            socket_path=str(tmp_path / "absent"))
    with pytest.raises(FileNotFoundError):
        r.start()


def test_volume_up_at_full_refuses_rather_than_clamping():
    """spotifyd sets initial_volume=100, above VOLUME_MAX

    Clamping there turned a volume-up press into a volume *cut*.
    """
    import buttons
    h = buttons.PlayerActionHandler.__new__(buttons.PlayerActionHandler)
    h.mixer_control = "PCM"
    h._current_volume = lambda: 100

    with patch("buttons.subprocess.run") as run, patch("buttons.utils") as u:
        h._set_volume(+10)

    run.assert_not_called()
    u.play_sound.assert_called_once_with("error")


def test_volume_down_at_zero_refuses():
    import buttons
    h = buttons.PlayerActionHandler.__new__(buttons.PlayerActionHandler)
    h.mixer_control = "PCM"
    h._current_volume = lambda: 0

    with patch("buttons.subprocess.run") as run, patch("buttons.utils") as u:
        h._set_volume(-10)

    run.assert_not_called()
    u.play_sound.assert_called_once_with("error")


# --- double press changes episode -------------------------------------------

class TestDoublePressEpisode:
    """A second press within the window means episode, not another track

    The risk this trades against: a child pressing next repeatedly to skip
    chapters must not land in a different episode. The window is short so
    deliberate presses, which run about a second apart, stay track-level.
    """

    def _series_player(self):
        player = MagicMock()
        player.next_episode = MagicMock()
        player.previous_episode = MagicMock()
        return player

    def _album_player(self):
        """A plain album card has no episode methods at all"""
        return MagicMock(spec=["next_track", "previous_track"])

    def _press(self, handler, player, action, at):
        handler.get_player = MagicMock(return_value=player)
        with patch("buttons.time.monotonic", return_value=at), \
                patch("buttons.utils"):
            handler.handle_action(action)

    def test_two_fast_presses_change_episode(self, handler):
        player = self._series_player()
        self._press(handler, player, "next_track", 100.0)
        self._press(handler, player, "next_track", 100.3)

        player.next_episode.assert_called_once()
        # The first press already skipped a track; the second supersedes it.
        assert player.next_track.call_count == 1

    def test_two_slow_presses_skip_two_tracks(self, handler):
        """Chapter skipping at a human pace must stay chapter skipping"""
        player = self._series_player()
        self._press(handler, player, "next_track", 100.0)
        self._press(handler, player, "next_track", 101.5)

        player.next_episode.assert_not_called()
        assert player.next_track.call_count == 2

    def test_a_third_press_starts_fresh(self, handler):
        player = self._series_player()
        self._press(handler, player, "next_track", 100.0)
        self._press(handler, player, "next_track", 100.3)
        player.next_track.reset_mock()

        self._press(handler, player, "next_track", 100.5)
        # Counting restarted, so this is a single press again.
        player.next_track.assert_called_once()
        assert player.next_episode.call_count == 1

    def test_previous_double_press_goes_back_an_episode(self, handler):
        player = self._series_player()
        self._press(handler, player, "previous_track", 50.0)
        self._press(handler, player, "previous_track", 50.2)

        player.previous_episode.assert_called_once()

    def test_album_cards_never_jump_episodes(self, handler):
        """Only series players expose the episode methods"""
        player = self._album_player()
        self._press(handler, player, "next_track", 10.0)
        self._press(handler, player, "next_track", 10.2)

        assert player.next_track.call_count == 2

    def test_different_actions_do_not_combine(self, handler):
        player = self._series_player()
        self._press(handler, player, "next_track", 5.0)
        self._press(handler, player, "previous_track", 5.2)

        player.next_episode.assert_not_called()
        player.previous_episode.assert_not_called()
