import os
import sys
import threading

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import pytest
from unittest.mock import MagicMock, patch

import buttons


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
    return h


def test_a_tap_does_nothing_and_is_silent(handler):
    """The prompt sound belonged to the double press; a hold needs no prompt"""
    with patch("buttons.utils") as mock_utils:
        handler.handle_action("shutdown")
        mock_utils.play_sound.assert_not_called()
        mock_utils.shutdown.assert_not_called()


def test_a_hold_shuts_down(handler):
    with patch("buttons.utils") as mock_utils:
        handler.handle_action("shutdown_hold")
        mock_utils.shutdown.assert_called_once()


def test_repeated_taps_never_shut_down(handler):
    """The old gesture counted events, so one press arriving twice confirmed
    itself - measured on toem2 at 23ms apart. A hold cannot be forged that
    way, and no number of taps may substitute for one.
    """
    with patch("buttons.utils") as mock_utils:
        for _ in range(5):
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


class _FakeSocket:
    """Yields batches of frames, then behaves as a closed socket: b"" forever

    Each element of *batches* is delivered by one recv(), which is how a real
    socket hands over everything that arrived while the previous batch was
    being handled - the case _coalesce() exists for.
    """
    def __init__(self, batches):
        self.batches = [b if isinstance(b, (list, tuple)) else [b]
                        for b in batches]
        self.reads_after_close = 0

    def recv(self, _n):
        if self.batches:
            return "".join(self.batches.pop(0)).encode()
        self.reads_after_close += 1
        if self.reads_after_close > 1000:
            raise AssertionError("busy loop: kept reading a closed socket")
        return b""


def test_closed_socket_does_not_spin(monkeypatch, tmp_path):
    """readline() returns "" forever once closed; continuing pegs the CPU"""
    r = _ir(monkeypatch, tmp_path)
    r._running = True
    f = _FakeSocket(["0001 00 KEY_NEXT devinput\n"])

    r._consume(f)   # must return, not spin

    assert f.reads_after_close == 1
    r.action_handler.handle_action.assert_called_once_with("next_track")


def test_held_key_fires_once_but_volume_repeats(monkeypatch, tmp_path):
    r = _ir(monkeypatch, tmp_path)
    r._running = True
    # Each frame in its own recv(), so nothing is batched. The volume repeat
    # is past the auto-repeat delay; the next_track repeat is dropped because
    # next_track is not repeatable.
    r._consume(_FakeSocket([
        "0001 00 KEY_NEXT devinput\n",
        "0001 01 KEY_NEXT devinput\n",
        "0002 00 KEY_VOLUMEUP devinput\n",
        "0002 09 KEY_VOLUMEUP devinput\n",
        "0003 00 KEY_NOTAKEY devinput\n",
    ]))
    actions = [c.args[0] for c in r.action_handler.handle_action.call_args_list]
    assert actions == ["next_track", "volume_up", "volume_up"]


def test_a_failing_action_does_not_end_ir_input(monkeypatch, tmp_path):
    """One bad action must not cost the remote for the rest of the uptime"""
    r = _ir(monkeypatch, tmp_path)
    r._running = True
    r.action_handler.handle_action.side_effect = [RuntimeError("boom"), None]

    r._consume(_FakeSocket([
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


# --- buttons are track-level only ----------------------------------------

class TestTapsNeverChangeEpisode:
    """A tap moves a track. Only a hold or a card re-scan moves an episode

    The old gesture compared timestamps taken in handle_action, but the IR
    reader blocks on the Spotify call, so what it actually measured was the
    API round-trip (TODO 30). It fired at random. The tests that covered it
    passed only because they patched buttons.time.monotonic, mocking away the
    very thing that broke it.
    """

    def _series_player(self):
        player = MagicMock()
        player.next_episode = MagicMock()
        player.previous_episode = MagicMock()
        return player

    def _press(self, handler, player, action, at):
        handler.get_player = MagicMock(return_value=player)
        with patch("buttons.time.monotonic", return_value=at), \
                patch("buttons.utils"):
            handler.handle_action(action)

    def test_fast_presses_skip_tracks_not_episodes(self, handler):
        player = self._series_player()
        self._press(handler, player, "next_track", 100.0)
        self._press(handler, player, "next_track", 100.3)

        assert player.next_track.call_count == 2
        player.next_episode.assert_not_called()

    def test_slow_presses_skip_tracks_too(self, handler):
        player = self._series_player()
        self._press(handler, player, "next_track", 100.0)
        self._press(handler, player, "next_track", 101.5)

        assert player.next_track.call_count == 2
        player.next_episode.assert_not_called()

    def test_previous_never_changes_episode(self, handler):
        player = self._series_player()
        self._press(handler, player, "previous_track", 50.0)
        self._press(handler, player, "previous_track", 50.2)

        assert player.previous_track.call_count == 2
        player.previous_episode.assert_not_called()


class _FakeButton:
    """Records how gpiozero was configured, and lets a test fire the events"""
    instances = []

    def __init__(self, pin, bounce_time=None, hold_time=None, hold_repeat=None):
        self.pin = pin
        self.bounce_time = bounce_time
        self.hold_time = hold_time
        self.hold_repeat = hold_repeat
        self.when_pressed = None
        self.when_held = None
        self.when_released = None
        _FakeButton.instances.append(self)

    def tap(self):
        if self.when_pressed:
            self.when_pressed()
        if self.when_released:
            self.when_released()

    def hold(self):
        if self.when_pressed:
            self.when_pressed()
        self.when_held()
        if self.when_released:
            self.when_released()


def _build_gpio_handler():
    _FakeButton.instances = []
    with patch("buttons.Button", _FakeButton), \
            patch.object(buttons.PlayerActionHandler, "_detect_mixer_control",
                         return_value=None):
        handler = buttons.GpioButtonHandler(
            lambda: None, lambda _p: None, "db", threading.Lock(), lambda: None)
    return handler, list(_FakeButton.instances)


def test_gpio_buttons_are_debounced():
    """gpiozero does not debounce by default, and the default is what bit us

    Without bounce_time one physical press fires when_pressed more than once -
    measured on toem2 at 23ms apart - which the old shutdown gesture read as
    its own confirmation and shut the box down with no confirmation at all.
    """
    _, created = _build_gpio_handler()
    assert created, "no buttons were created"
    for b in created:
        assert b.bounce_time == buttons.GpioButtonHandler.BUTTON_BOUNCE_TIME, (
            "pin %s was created without debouncing" % b.pin)


def test_only_buttons_with_a_hold_action_are_wired_for_hold():
    """hold_repeat must be False: one hold is one action, not one per second

    Verified against the real button on toem2 2026-08-31 - a 178ms tap fired
    when_pressed only, a hold fired when_held once at exactly 1.001s.
    """
    _, created = _build_gpio_handler()
    by_pin = {b.pin: b for b in created}

    for pin, action in buttons.GPIO_ACTIONS.items():
        button = by_pin[pin]
        if action in buttons.PlayerActionHandler.HOLD_ACTIONS:
            assert button.when_held is not None, (
                "%s has a hold action but no when_held" % action)
            assert button.hold_time == buttons.PlayerActionHandler.HOLD_TIME
            assert button.hold_repeat is False, "a hold must fire once"
            # The tap fires on release, not press: when_pressed would act
            # before we know which gesture it is.
            assert button.when_pressed is None, (
                "%s acts on press, so a hold would do both" % action)
            assert button.when_released is not None
        else:
            assert button.when_held is None, (
                "%s has no hold action but was wired for hold" % action)
            assert button.when_pressed is not None


def _gpio_button(action):
    handler, created = _build_gpio_handler()
    handler.action_handler.handle_action = MagicMock()
    pin = [p for p, a in buttons.GPIO_ACTIONS.items() if a == action][0]
    return handler, [b for b in created if b.pin == pin][0]


def _fired(handler):
    return [c.args[0] for c in
            handler.action_handler.handle_action.call_args_list]


def test_tapping_fires_only_the_tap_action():
    handler, button = _gpio_button("next_track")
    button.tap()
    assert _fired(handler) == ["next_track"]


def test_holding_fires_only_the_hold_action():
    """when_pressed fired the instant the button went down, so a hold did
    both: holding next skipped a track *and* changed episode, and on an album
    it skipped a track where it should have done nothing at all.
    """
    handler, button = _gpio_button("next_track")
    button.hold()
    assert _fired(handler) == ["next_episode"], (
        "a hold must not also fire the tap action")


def test_a_hold_then_a_tap_both_behave():
    """The held flag must reset, or every later tap is swallowed"""
    handler, button = _gpio_button("previous_track")
    button.hold()
    button.tap()
    assert _fired(handler) == ["previous_episode", "previous_track"]


def test_a_backlog_of_repeats_collapses_to_one_step(monkeypatch, tmp_path):
    """Held volume must stop when the key is released, not seconds later

    lircd emits a repeat every ~110ms; one volume step costs ~250ms of amixer.
    Acting on every frame cannot keep up, so the surplus queues in the socket
    and the volume goes on moving after release - measured on toem 2026-08-31
    at 45 frames consumed at a median of 244ms.
    """
    r = _ir(monkeypatch, tmp_path)
    r._running = True

    # One recv() delivering everything that piled up during a hold.
    burst = ["0002 00 KEY_VOLUMEDOWN toem_nec\n"] + [
        "0002 %02x KEY_VOLUMEDOWN toem_nec\n" % n for n in range(1, 16)]
    r._consume(_FakeSocket([burst]))

    actions = [c.args[0] for c in r.action_handler.handle_action.call_args_list]
    assert actions == ["volume_down"], (
        "16 buffered frames became %d steps; the ramp would outlive the press"
        % len(actions))


def test_each_batch_advances_the_ramp_once(monkeypatch, tmp_path):
    """Collapsing must not stall the ramp - a held key still steps per batch"""
    r = _ir(monkeypatch, tmp_path)
    r._running = True

    # Counts past the auto-repeat delay, so each batch is a real ramp step.
    r._consume(_FakeSocket([
        ["0002 00 KEY_VOLUMEDOWN toem_nec\n", "0002 05 KEY_VOLUMEDOWN toem_nec\n"],
        ["0002 06 KEY_VOLUMEDOWN toem_nec\n", "0002 07 KEY_VOLUMEDOWN toem_nec\n"],
        ["0002 08 KEY_VOLUMEDOWN toem_nec\n"],
    ]))

    actions = [c.args[0] for c in r.action_handler.handle_action.call_args_list]
    assert actions == ["volume_down", "volume_down", "volume_down"]


def test_distinct_presses_in_one_batch_are_all_kept(monkeypatch, tmp_path):
    """A 00 frame is a real press and must never be collapsed away"""
    r = _ir(monkeypatch, tmp_path)
    r._running = True

    r._consume(_FakeSocket([[
        "0002 00 KEY_VOLUMEDOWN toem_nec\n",
        "0002 00 KEY_VOLUMEDOWN toem_nec\n",
        "0002 00 KEY_VOLUMEDOWN toem_nec\n",
    ]]))

    actions = [c.args[0] for c in r.action_handler.handle_action.call_args_list]
    assert actions == ["volume_down"] * 3


def test_a_frame_split_across_reads_is_not_lost(monkeypatch, tmp_path):
    """recv() boundaries fall anywhere, including mid-frame"""
    r = _ir(monkeypatch, tmp_path)
    r._running = True

    r._consume(_FakeSocket(["0001 00 KEY_N", "EXT devinput\n"]))

    actions = [c.args[0] for c in r.action_handler.handle_action.call_args_list]
    assert actions == ["next_track"]


# --- holding next/previous: a whole episode -------------------------------

def _series(playing=True):
    player = MagicMock()
    player.is_series = True
    return player


def _album():
    player = MagicMock()
    player.is_series = False
    return player


def test_holding_next_advances_an_episode(handler):
    player = _series()
    handler.get_player = MagicMock(return_value=player)
    with patch("buttons.utils"):
        handler.handle_action("next_episode")
    player.next_episode.assert_called_once()
    player.next_track.assert_not_called()


def test_holding_previous_goes_back_an_episode(handler):
    """The only way back. Re-scanning the card advances but never reverses,
    so previous_episode() was implemented and reachable from nothing.
    """
    player = _series()
    handler.get_player = MagicMock(return_value=player)
    with patch("buttons.utils"):
        handler.handle_action("previous_episode")
    player.previous_episode.assert_called_once()
    player.previous_track.assert_not_called()


def test_holding_on_an_album_declines_without_an_error(handler):
    """An album has no episodes. The child pressed a real button correctly,
    so this is not an error - and restarting the album instead would throw
    away their place in a twenty-minute Hoerspiel.
    """
    player = _album()
    handler.get_player = MagicMock(return_value=player)
    with patch("buttons.utils") as mock_utils:
        handler.handle_action("next_episode")
        handler.handle_action("previous_episode")

    player.next_episode.assert_not_called()
    player.previous_episode.assert_not_called()
    player.next_track.assert_not_called()
    player.restart_playback.assert_not_called()
    assert [c.args[0] for c in mock_utils.play_sound.call_args_list] == [
        "nothing_here", "nothing_here"]


def test_holding_with_no_player_is_an_error(handler):
    handler.get_player = MagicMock(return_value=None)
    with patch("buttons.utils") as mock_utils:
        handler.handle_action("next_episode")
    mock_utils.play_sound.assert_called_once_with("error")


# --- holding an IR key: the repeat counter is the clock -------------------

def _ir_hold_frames(key, count):
    """A press followed by *count* repeats, as lircd emits them"""
    return ["0002 %02x %s toem_nec\n" % (n, key) for n in range(count + 1)]


def test_a_held_ir_key_fires_the_hold_action_once(monkeypatch, tmp_path):
    """Counting frames, not elapsed time: the remote sets the cadence, so a
    slow handler cannot stretch it. Item 24's double press measured elapsed
    time and ended up measuring the Spotify round-trip instead.
    """
    r = _ir(monkeypatch, tmp_path)
    r._running = True
    r._consume(_FakeSocket([_ir_hold_frames("KEY_POWER", 30)]))

    actions = [c.args[0] for c in r.action_handler.handle_action.call_args_list]
    assert actions == ["shutdown", "shutdown_hold"], (
        "31 frames should be one tap and one hold, got %r" % (actions,))


def test_a_brief_ir_press_does_not_reach_the_hold(monkeypatch, tmp_path):
    r = _ir(monkeypatch, tmp_path)
    r._running = True
    r._consume(_FakeSocket([_ir_hold_frames("KEY_POWER", 2)]))

    actions = [c.args[0] for c in r.action_handler.handle_action.call_args_list]
    assert actions == ["shutdown"]


def test_a_second_ir_hold_fires_again(monkeypatch, tmp_path):
    """A fresh 00 frame rearms, so two holds are two actions"""
    r = _ir(monkeypatch, tmp_path)
    r._running = True
    r._consume(_FakeSocket([_ir_hold_frames("KEY_NEXT", 30),
                            _ir_hold_frames("KEY_NEXT", 30)]))

    actions = [c.args[0] for c in r.action_handler.handle_action.call_args_list]
    assert actions == ["next_track", "next_episode",
                       "next_track", "next_episode"]


def test_an_ir_hold_split_across_batches_still_fires_once(monkeypatch, tmp_path):
    """A burst spans recv() boundaries; the hold must not fire per batch"""
    r = _ir(monkeypatch, tmp_path)
    r._running = True
    frames = _ir_hold_frames("KEY_VOLUMEDOWN", 0) + _ir_hold_frames("KEY_NEXT", 30)
    r._consume(_FakeSocket([frames[:5], frames[5:20], frames[20:]]))

    actions = [c.args[0] for c in r.action_handler.handle_action.call_args_list]
    assert actions.count("next_episode") == 1, actions


def test_a_short_press_of_volume_steps_once(monkeypatch, tmp_path):
    """One press must be one step

    Without a delay the first repeat frame - ~110ms in - already counted as a
    second step, so an ordinary press of volume down gave three steps on toem
    while a quicker press of volume up gave one. Observed 2026-08-31.
    """
    r = _ir(monkeypatch, tmp_path)
    r._running = True
    # A press plus two repeats: ~220ms, a perfectly ordinary tap.
    r._consume(_FakeSocket([_ir_hold_frames("KEY_VOLUMEDOWN", 2)]))

    actions = [c.args[0] for c in r.action_handler.handle_action.call_args_list]
    assert actions == ["volume_down"]


def test_holding_volume_still_ramps(monkeypatch, tmp_path):
    """The delay must not disable the ramp, only postpone it"""
    r = _ir(monkeypatch, tmp_path)
    r._running = True
    frames = _ir_hold_frames("KEY_VOLUMEDOWN", 12)
    # Delivered one at a time, as they arrive when the handler keeps up.
    r._consume(_FakeSocket(list(frames)))

    actions = [c.args[0] for c in r.action_handler.handle_action.call_args_list]
    assert len(actions) > 1, "a held key must keep stepping"
    assert set(actions) == {"volume_down"}


def test_volume_at_an_end_stop_makes_one_sound_not_two(handler):
    """The beep used to play first and the refusal second - two noises"""
    handler.mixer_control = "PCM"
    with patch.object(handler, "_current_volume", return_value=0), \
            patch("buttons.utils") as mock_utils:
        handler.handle_action("volume_down")

    assert [c.args[0] for c in mock_utils.play_sound.call_args_list] == ["error"]


def test_volume_beeps_after_the_change_not_during_it(handler):
    """Beeping first meant amixer ran while the tone was still sounding, so
    the beep changed level mid-play. Afterwards it is one clean sound, at the
    level just selected.
    """
    handler.mixer_control = "PCM"
    order = []
    with patch.object(handler, "_current_volume", return_value=50), \
            patch("buttons.subprocess.run",
                  side_effect=lambda *a, **k: order.append("amixer")), \
            patch("buttons.utils") as mock_utils:
        mock_utils.play_sound.side_effect = lambda name: order.append(name)
        handler.handle_action("volume_down")

    assert order == ["amixer", "volume_down"]
