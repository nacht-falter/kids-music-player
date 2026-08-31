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

    # How long a shutdown confirmation stays armed. Without a timeout the first
    # press arms the device indefinitely, so an accidental press hours earlier
    # turns the next one into an immediate shutdown with no confirmation.
    SHUTDOWN_CONFIRM_TIMEOUT = 5

    # ...and how soon a confirmation may arrive. The window above bounds only
    # how *late* the second press can be; without a lower bound two events
    # 23ms apart confirm each other, which is what a bouncing contact delivers
    # for one physical press. Measured on toem2 over 66 shutdowns: gaps of 23,
    # 63 and 71ms against a median of 972ms, and nothing human between 71 and
    # 141ms. 150ms sits in that gap - above any bounce seen, below the fastest
    # plausible deliberate double press.
    MIN_CONFIRM_INTERVAL = 0.15

    def __init__(self, get_player, set_player, database_url, player_lock, reset_last_activity):
        self.get_player = get_player
        self.set_player = set_player
        self.database_url = database_url
        self.player_lock = player_lock
        self.reset_last_activity = reset_last_activity

        # For shutdown confirmation and consecutive actions
        self.last_action = None
        self.consecutive_count = 0
        self.shutdown_lock = threading.Lock()
        self.confirm_timer = None
        self.confirm_armed_at = None

        # Detected once: the control does not change while we run, and probing
        # on every keypress would add a subprocess to each one.
        self.mixer_control = self._detect_mixer_control()
        if self.mixer_control:
            logging.info("Volume control: ALSA '%s'", self.mixer_control)

        # Map action names to handler methods
        self.action_map = {
            "shutdown": self._handle_shutdown,
            "toggle_playback": self._handle_toggle_playback,
            "next_track": self._handle_next_track,
            "previous_track": self._handle_previous_track,
            "volume_up": self._handle_volume_up,
            "volume_down": self._handle_volume_down,
        }

    def handle_action(self, action):
        self.reset_last_activity()

        if self.last_action == action:
            self.consecutive_count += 1
        else:
            self.last_action = action
            self.consecutive_count = 1

        handler = self.action_map.get(action)
        if handler:
            handler()
        else:
            logging.warning(f"Unknown action: {action}")

    def _handle_shutdown(self):
        # shutdown_lock is taken before player_lock here, and nothing takes
        # them the other way round - the remaining handlers only take
        # player_lock. Keep it that way.
        with self.shutdown_lock:
            if self.consecutive_count < 2:
                logging.info(
                    "Shutdown button pressed once. Confirming shutdown.")
                self.confirm_armed_at = time.monotonic()
                utils.play_sound("confirm_shutdown")

                # Cancelled on a second press and marked daemon, so a pending
                # timer neither fires against a stale state nor holds the
                # process open for its duration at exit.
                if self.confirm_timer:
                    self.confirm_timer.cancel()
                self.confirm_timer = threading.Timer(
                    self.SHUTDOWN_CONFIRM_TIMEOUT,
                    self._reset_shutdown_confirmation)
                self.confirm_timer.daemon = True
                self.confirm_timer.start()
            else:
                # Too soon to be a second press by a person, so it is the same
                # press arriving twice - a bouncing contact, or lircd starting
                # a fresh repeat count after a brief IR dropout. Ignore it and
                # leave the confirmation armed, so the real second press still
                # works.
                since = (time.monotonic() - self.confirm_armed_at
                         if self.confirm_armed_at is not None else None)
                if since is not None and since < self.MIN_CONFIRM_INTERVAL:
                    logging.info(
                        "Ignoring a shutdown confirmation %.0fms after the "
                        "first press; too fast to be a second press. Still "
                        "armed.", since * 1000)
                    return

                logging.info("Shutdown confirmed. Shutting down.")
                if self.confirm_timer:
                    self.confirm_timer.cancel()
                    self.confirm_timer = None
                self.confirm_armed_at = None
                with self.player_lock:
                    utils.shutdown(self.get_player())
                self.consecutive_count = 0
                self.last_action = None

    def _reset_shutdown_confirmation(self):
        """Disarm the confirmation. Called from the timer thread."""
        with self.shutdown_lock:
            logging.info("Shutdown confirmation expired without a second press.")
            self.confirm_timer = None
            self.confirm_armed_at = None
            self.consecutive_count = 0
            self.last_action = None

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
            button = Button(pin, bounce_time=self.BUTTON_BOUNCE_TIME)
            button.when_pressed = lambda a=action: self.action_handler.handle_action(
                a)
            # Keep reference to avoid garbage collection:
            self.buttons.append(button)


class IrReceiver:
    """Handles IR remote control input via LIRC."""

    # Actions where holding the key should keep firing.
    REPEATABLE_ACTIONS = {"volume_up", "volume_down"}

    def __init__(self, get_player, set_player, database_url, player_lock, reset_last_activity, socket_path='/var/run/lirc/lircd'):
        self.socket_path = socket_path
        self._running = False
        self._thread = None
        self._stopping = threading.Event()

        self.action_handler = PlayerActionHandler(
            get_player, set_player, database_url, player_lock, reset_last_activity
        )

        self.key_map = IR_ACTIONS

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
        actions = []
        for line in lines:
            parts = line.split()
            if len(parts) < 3:
                continue

            repeat, key = parts[1], parts[2]
            action = self.key_map.get(key)
            if not action:
                continue
            if repeat != "00":
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
