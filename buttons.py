import logging
import os
import re
import socket
import sqlite3
import subprocess
import threading
import time

import utils

try:
    from gpiozero import Button
except ImportError:
    Button = None

try:
    import led
except ImportError:
    led = None


class PlayerActionHandler:
    """Handles player-related actions that can be triggered by different input methods."""

    # How long the shutdown button must be held. This replaces the old
    # double-press confirmation, which counted events and so could not tell a
    # deliberate second press from the same press arriving twice - measured on
    # toem2 at 23ms apart, one physical press shutting the box down with no
    # confirmation at all. A hold cannot be forged that way: contact bounce is
    # milliseconds and an IR dropout restarts the repeat count rather than
    # sustaining it. It is also the universal device idiom.
    HOLD_TIME = 1.0

    # Actions a hold turns into something else. The plain press still fires
    # first - for shutdown that is the acknowledging sound, so a tap says
    # "this is the power button, keep holding" instead of doing nothing.
    #
    # The rule the child learns is one rule: a hold jumps further than a tap.
    # Tap next/previous moves a track, holding it moves a whole episode. That
    # is also the only way back through a series - re-scanning the card
    # advances an episode but has no reverse, so previous_episode() has been
    # implemented and unreachable.
    #
    # No device convention was copied here because none exists: press-and-hold
    # is fast-forward on some players and "skip to start of track" on others,
    # and the two closest boxes - Toniebox and Yoto - give seeking a separate
    # gesture (a tilt, a knob) rather than a duration. Checked 2026-08-31.
    HOLD_ACTIONS = {
        "shutdown": "shutdown_hold",
        "next_track": "next_episode",
        "previous_track": "previous_episode",
    }

    def __init__(self, get_player, set_player, database_url, player_lock, reset_last_activity):
        self.get_player = get_player
        self.set_player = set_player
        self.database_url = database_url
        self.player_lock = player_lock
        self.reset_last_activity = reset_last_activity

        # Serialises the shutdown itself; there is no confirmation state to
        # guard any more.
        self.shutdown_lock = threading.Lock()

        # Detected once: the control does not change while we run, and probing
        # on every keypress would add a subprocess to each one.
        self.mixer_control = self._detect_mixer_control()
        if self.mixer_control:
            logging.info("Volume control: ALSA '%s'", self.mixer_control)

        # Map action names to handler methods
        self.action_map = {
            "shutdown": self._handle_shutdown_pressed,
            "shutdown_hold": self._handle_shutdown_confirmed,
            "toggle_playback": self._handle_toggle_playback,
            "next_track": self._handle_next_track,
            "previous_track": self._handle_previous_track,
            "next_episode": self._handle_next_episode,
            "previous_episode": self._handle_previous_episode,
            "volume_up": self._handle_volume_up,
            "volume_down": self._handle_volume_down,
        }

    def handle_action(self, action):
        self.reset_last_activity()

        handler = self.action_map.get(action)
        if handler:
            handler()
        else:
            logging.warning(f"Unknown action: {action}")

    def _handle_shutdown_pressed(self):
        """A tap on the power button: acknowledge it and do nothing else"""
        logging.info("Shutdown button pressed. Hold to shut down.")
        utils.play_sound("confirm_shutdown")

    def _handle_shutdown_confirmed(self):
        """The button was held long enough to mean it"""
        # shutdown_lock is taken before player_lock here, and nothing takes
        # them the other way round - the remaining handlers only take
        # player_lock. Keep it that way.
        with self.shutdown_lock:
            logging.info("Shutdown button held. Shutting down.")
            with self.player_lock:
                utils.shutdown(self.get_player())

    def _handle_toggle_playback(self):
        with self.player_lock:
            player = self.get_player()
            if player:
                logging.info("Toggle playback action triggered.")
                utils.play_sound("toggle_playback")
                if player.playback_started:
                    player.toggle_playback()
                else:
                    player.play()
            else:
                utils.play_sound("toggle_playback")
                self._create_and_play_last_player()

    # Buttons are track-level only. Changing episode used to be a double press,
    # but the gap it measured was the Spotify round-trip rather than the
    # interval between presses (TODO 30), so it fired at random. Re-scanning
    # the card advances an episode instead - a discrete event with no window.

    def _handle_next_track(self):
        with self.player_lock:
            player = self.get_player()
            if player:
                logging.info("Next track action triggered.")
                utils.play_sound("next_track")
                player.next_track()
            else:
                logging.warning(
                    "Player is not initialized. Cannot skip to next track.")
                utils.play_sound("error")

    def _handle_previous_track(self):
        with self.player_lock:
            player = self.get_player()
            if player:
                logging.info("Previous track action triggered.")
                utils.play_sound("previous_track")
                player.previous_track()
            else:
                logging.warning(
                    "Player is not initialized. Cannot skip to previous track.")
                utils.play_sound("error")

    # ALSA rather than Spotify Connect or mpc: it is the only layer that
    # covers both sources and works while nothing is streaming.
    #
    # The control is detected, not hardcoded. Raspberry Pi OS has renamed it
    # across releases - the bash version this replaces used "Headphone", which
    # no longer exists on this hardware, so that script's volume had silently
    # stopped working. MIXER_CONTROL in .env overrides the detection.
    MIXER_CANDIDATES = ("PCM", "Headphone", "Master", "Speaker", "Digital")
    VOLUME_STEP = 10
    VOLUME_MAX = 90

    def _detect_mixer_control(self):
        """Pick a usable ALSA control, or None if there is none"""
        configured = os.getenv("MIXER_CONTROL")
        try:
            listed = subprocess.run(["amixer", "scontrols"],
                                    capture_output=True, text=True,
                                    timeout=5).stdout
        except (OSError, subprocess.SubprocessError) as e:
            logging.error("Could not list ALSA controls: %s", e)
            return None

        available = re.findall(r"'([^']+)'", listed)
        if configured:
            if configured in available:
                return configured
            logging.warning(
                "MIXER_CONTROL=%r is not among the available controls %s",
                configured, available)

        for name in self.MIXER_CANDIDATES:
            if name in available:
                return name

        # Anything at all beats failing, since the names vary by device.
        if available:
            return available[0]

        logging.error("No ALSA mixer controls found; volume keys will not work")
        return None

    def _current_volume(self):
        """Current mixer volume as a percentage, or None if unreadable"""
        if not self.mixer_control:
            return None
        try:
            output = subprocess.run(
                ["amixer", "-M", "get", self.mixer_control],
                capture_output=True, text=True, timeout=5).stdout
            match = re.search(r"\[(\d{1,3})%\]", output)
            return int(match.group(1)) if match else None
        except (OSError, subprocess.SubprocessError) as e:
            logging.error("Could not read the volume: %s", e)
            return None

    def _set_volume(self, change):
        """Nudge the mixer by `change` percent, clamped to VOLUME_MAX"""
        current = self._current_volume()
        if current is None:
            utils.play_sound("error")
            return

        # Refuse rather than clamp when already past an end stop. Clamping was
        # worse than doing nothing: spotifyd sets initial_volume to 100, so a
        # volume-*up* press at 100% would have pulled it down to VOLUME_MAX.
        if change > 0 and current >= self.VOLUME_MAX:
            utils.play_sound("error")
            return
        if change < 0 and current <= 0:
            utils.play_sound("error")
            return

        target = max(0, min(self.VOLUME_MAX, current + change))
        if target == current:
            utils.play_sound("error")
            return

        try:
            subprocess.run(
                ["amixer", "-M", "-q", "set", self.mixer_control, f"{target}%"],
                check=True, timeout=5)
            logging.info("Volume %d%% -> %d%% (%s)",
                         current, target, self.mixer_control)
        except (OSError, subprocess.SubprocessError) as e:
            logging.error("Could not set the volume: %s", e)
            utils.play_sound("error")

    def _handle_episode(self, step, label):
        """Move a whole episode, or decline politely if there is nowhere to go

        A card that is a plain album has no episodes. Declining with a neutral
        tone rather than the error sound is deliberate: the child pressed a
        real button in a legitimate way and there is simply nothing there, so
        "you did something wrong" would be both untrue and discouraging.

        Restarting the album instead - what re-scanning the card does - was
        considered and rejected: on six of the nine album cards on toem2 that
        would throw away a child's place in a twenty-minute Hörspiel, and the
        card already offers that gesture for anyone who wants it.
        """
        with self.player_lock:
            player = self.get_player()
            if not player:
                logging.warning("Player is not initialized. Cannot change episode.")
                utils.play_sound("error")
                return
            if not getattr(player, "is_series", False):
                logging.info("Not a series; %s does nothing here.", label)
                utils.play_sound("nothing_here")
                return

            logging.info("%s action triggered.", label.capitalize())
            utils.play_sound("next_track" if step > 0 else "previous_track")
            if step > 0:
                player.next_episode()
            else:
                player.previous_episode()

    def _handle_next_episode(self):
        self._handle_episode(+1, "next episode")

    def _handle_previous_episode(self):
        self._handle_episode(-1, "previous episode")

    def _handle_volume_up(self):
        utils.play_sound("volume_up")
        self._set_volume(+self.VOLUME_STEP)

    def _handle_volume_down(self):
        utils.play_sound("volume_down")
        self._set_volume(-self.VOLUME_STEP)

    def _create_and_play_last_player(self):
        try:
            with sqlite3.connect(self.database_url) as db:
                music_data = utils.get_music_data(
                    db, utils.get_last_played_rfid(db))
                if not music_data:
                    logging.warning("No last played data to create player.")
                    utils.play_sound("error")
                    return

                if led:
                    stop_event, thread = led.start_flashing(23, 0)
                else:
                    stop_event, thread = None, None

                try:
                    new_player = utils.create_player(music_data)
                    if new_player:
                        self.set_player(new_player)
                        # Not play(): spotifyd outlives this process, so the
                        # speaker may already be playing this very album with
                        # the controller merely restarted underneath it. play()
                        # would seek to the stored position, turning a pause
                        # press into a jump to somewhere the child did not
                        # expect. toggle_playback() looks first and pauses,
                        # resumes or reclaims as the live state requires.
                        new_player.toggle_playback()
                except Exception:
                    logging.exception("Failed to create player.")
                    utils.play_sound("playback_error")
                finally:
                    if led and stop_event and thread:
                        led.stop_flashing(stop_event, thread)
        except Exception:
            logging.exception("Failed to access database.")
            utils.play_sound("playback_error")


class GpioButtonHandler:
    """Handles GPIO button presses."""

    # State changes ignored after an edge. Mechanical dome switches bounce for
    # a few ms; 100ms outlasts that with margin and stays well below the
    # fastest deliberate double press observed (141ms).
    BUTTON_BOUNCE_TIME = 0.1

    def __init__(self, get_player, set_player, database_url, player_lock, reset_last_activity):
        if not Button:
            raise RuntimeError(
                "gpiozero.Button is not available. Cannot initialize ButtonHandler.")

        self.action_handler = PlayerActionHandler(
            get_player, set_player, database_url, player_lock, reset_last_activity
        )

        self.buttons = []
        for pin, action in GPIO_ACTIONS.items():
            # gpiozero does not debounce by default, so a bouncing contact
            # fires when_pressed more than once for a single physical press.
            # On the shutdown button that reads as its own confirmation -
            # measured on toem2 at 23ms - and the box goes down without asking.
            button = Button(pin, bounce_time=self.BUTTON_BOUNCE_TIME,
                            hold_time=PlayerActionHandler.HOLD_TIME,
                            hold_repeat=False)
            button.when_pressed = lambda a=action: self.action_handler.handle_action(
                a)

            # hold_repeat=False, so this fires once per hold rather than
            # every hold_time. Verified on toem2's own button 2026-08-31: a
            # 178ms tap fired when_pressed only, a hold fired when_held once
            # at exactly 1.001s. gpiozero 1.6.2 does the timing in its own
            # thread, so a slow handler cannot distort it - which is what
            # sank the double-press gesture in item 24.
            hold = PlayerActionHandler.HOLD_ACTIONS.get(action)
            if hold:
                button.when_held = lambda h=hold: self.action_handler.handle_action(
                    h)

            # Keep reference to avoid garbage collection:
            self.buttons.append(button)


class IrReceiver:
    """Handles IR remote control input via LIRC."""

    # Actions where holding the key should keep firing.
    REPEATABLE_ACTIONS = {"volume_up", "volume_down"}

    # lircd emits a repeat about every 110ms while a key is held, so this many
    # of them is roughly PlayerActionHandler.HOLD_TIME. Counting frames rather
    # than measuring elapsed time is deliberate: the remote sets the cadence,
    # so a slow handler cannot stretch it. Item 24's double press measured
    # elapsed time and ended up measuring the Spotify round-trip instead.
    IR_REPEAT_INTERVAL = 0.11

    def __init__(self, get_player, set_player, database_url, player_lock, reset_last_activity, socket_path='/var/run/lirc/lircd'):
        self.socket_path = socket_path
        self._running = False
        self._thread = None
        self._stopping = threading.Event()

        self.action_handler = PlayerActionHandler(
            get_player, set_player, database_url, player_lock, reset_last_activity
        )

        self.key_map = IR_ACTIONS

        # Actions whose hold has already fired, so one hold produces one
        # action however long it is held. Cleared by the next 00 frame, which
        # is a fresh press.
        self._held = set()

    # How long to wait before reconnecting after the socket drops.
    RECONNECT_DELAY = 5

    def _read_loop(self):
        """Read IR frames, reconnecting if lircd goes away

        Reconnects rather than returning: the remote is this device's only
        control besides the RFID reader, so a dropped socket must not silently
        end IR input for the rest of the uptime.
        """
        while self._running:
            try:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                    sock.connect(self.socket_path)
                    logging.info("Listening for IR input...")
                    self._consume(sock)
            except OSError as e:
                logging.error("LIRC socket error: %s", e)

            if self._running:
                logging.info("Reconnecting to LIRC in %ds", self.RECONNECT_DELAY)
                # Event.wait rather than sleep so stop() is not left waiting.
                self._stopping.wait(self.RECONNECT_DELAY)

    def _coalesce(self, lines):
        """The actions a batch of frames is actually worth taking

        lircd sends "<code> <repeat> <key> <remote>", with repeat counting up
        while a key is held. Acting on every frame would skip a dozen tracks
        from one long press, so only the first counts - except volume, where
        holding to ramp is the point.

        Repeats arrive every ~110ms and one volume step costs ~250ms of
        amixer, so acting on each one cannot keep up: the surplus queues in
        the socket and the volume goes on moving for seconds after the key is
        released. Measured on toem 2026-08-31 - 45 frames consumed at a median
        of 244ms against lircd's 110ms. So a run of repeats for the same
        action collapses to a single action, and the ramp advances once per
        batch instead of building a backlog.

        A `00` frame is a distinct press and is never dropped.
        """
        hold_after = max(
            1, round(PlayerActionHandler.HOLD_TIME / self.IR_REPEAT_INTERVAL))

        actions = []
        for line in lines:
            parts = line.split()
            if len(parts) < 3:
                continue

            repeat, key = parts[1], parts[2]
            action = self.key_map.get(key)
            if not action:
                continue
            try:
                count = int(repeat, 16)
            except ValueError:
                continue

            if count == 0:            # a fresh press
                self._held.discard(action)
                actions.append((key, action))
                continue

            hold = PlayerActionHandler.HOLD_ACTIONS.get(action)
            if hold:
                # Held long enough, and not already fired for this burst.
                if action not in self._held and count >= hold_after:
                    self._held.add(action)
                    actions.append((key, hold))
                continue

            if action not in self.REPEATABLE_ACTIONS:
                continue
            if actions and actions[-1][1] == action:
                continue  # same burst; one is already queued
            actions.append((key, action))
        return actions

    def _consume(self, sock):
        """Dispatch frames until the socket closes

        Reads the socket directly rather than through makefile(): a buffered
        reader hides how many frames are already waiting, and that backlog is
        exactly what _coalesce() exists to collapse. Each pass takes
        everything that arrived while the previous batch was being handled.
        """
        buffer = b""
        while self._running:
            chunk = sock.recv(4096)
            if not chunk:
                # recv() returns b"" immediately and forever once the peer has
                # closed. Continuing here spins the CPU at 100%.
                logging.warning("LIRC closed the connection")
                return

            buffer += chunk
            lines = buffer.split(b"\n")
            # A trailing fragment is not a frame yet; hold it for the next read.
            buffer = lines.pop()

            for key, action in self._coalesce(
                    line.decode(errors="replace").strip() for line in lines):
                logging.info("Received key: %s -> action: %s", key, action)
                try:
                    self.action_handler.handle_action(action)
                except Exception as e:
                    # One failing action must not end IR input for good.
                    logging.exception("Action %s failed: %s", action, e)

    def start(self):
        if not os.path.exists(self.socket_path):
            raise FileNotFoundError(
                f"LIRC socket not found at {self.socket_path}")

        self._running = True
        self._stopping.clear()
        self._thread = threading.Thread(target=self._read_loop, daemon=True)
        self._thread.start()
        logging.info("IR receiver started")

    def stop(self):
        self._running = False
        # Releases the reconnect wait, so stop() does not block for up to
        # RECONNECT_DELAY. The blocking readline() is left to the daemon
        # thread, which dies with the process.
        self._stopping.set()
        if self._thread:
            self._thread.join(timeout=self.RECONNECT_DELAY + 1)
        logging.info("IR receiver stopped")


def create_button_handler(handler_type, get_player, set_player, database_url, player_lock, reset_last_activity):
    """Create the specified input handler type."""

    if handler_type == "gpio":
        if not Button:
            raise RuntimeError("gpiozero.Button is not available")
        return GpioButtonHandler(get_player, set_player, database_url, player_lock, reset_last_activity)

    elif handler_type == "ir":
        # start() checks the socket itself; duplicating the path here let the
        # two drift apart.
        handler = IrReceiver(get_player, set_player,
                             database_url, player_lock, reset_last_activity)
        handler.start()
        return handler

    else:
        raise ValueError(f"Unknown handler type: {handler_type}")


# Which control triggers which action, per input device. Explicit rather than
# positional: the two devices genuinely differ - toem2 has a hardware volume
# pot so its GPIO handler needs no volume actions, while the IR remote has
# volume buttons and no pot - and the remote sends two different keys that both
# mean "toggle", which a positional mapping cannot express.
GPIO_ACTIONS = {
    3: "shutdown",
    17: "toggle_playback",
    27: "next_track",
    22: "previous_track",
}

# Key names as the remote actually sends them, taken from the working lircrc of
# the bash version this replaces. KEY_POWER was "stop" there, with a double
# press to shut down; it is shutdown-with-confirmation here so both devices
# behave the same way.
IR_ACTIONS = {
    "KEY_POWER": "shutdown",
    "KEY_PLAY": "toggle_playback",
    "KEY_PAUSE": "toggle_playback",
    "KEY_NEXT": "next_track",
    "KEY_PREVIOUS": "previous_track",
    "KEY_VOLUMEUP": "volume_up",
    "KEY_VOLUMEDOWN": "volume_down",
}
