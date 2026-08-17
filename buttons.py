import logging
import os
import socket
import sqlite3
import threading

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

        # Map action names to handler methods
        self.action_map = {
            "shutdown": self._handle_shutdown,
            "toggle_playback": self._handle_toggle_playback,
            "next_track": self._handle_next_track,
            "previous_track": self._handle_previous_track,
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
                logging.info("Shutdown confirmed. Shutting down.")
                if self.confirm_timer:
                    self.confirm_timer.cancel()
                    self.confirm_timer = None
                with self.player_lock:
                    utils.shutdown(self.get_player())
                self.consecutive_count = 0
                self.last_action = None

    def _reset_shutdown_confirmation(self):
        """Disarm the confirmation. Called from the timer thread."""
        with self.shutdown_lock:
            logging.info("Shutdown confirmation expired without a second press.")
            self.confirm_timer = None
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
                        new_player.play()
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

    def __init__(self, get_player, set_player, database_url, player_lock, reset_last_activity):
        if not Button:
            raise RuntimeError(
                "gpiozero.Button is not available. Cannot initialize ButtonHandler.")

        self.action_handler = PlayerActionHandler(
            get_player, set_player, database_url, player_lock, reset_last_activity
        )

        gpio_pins = [3, 17, 27, 22]
        self.gpio_map = map_actions(gpio_pins)

        self.buttons = []
        for pin, action in self.gpio_map.items():
            button = Button(pin)
            button.when_pressed = lambda a=action: self.action_handler.handle_action(
                a)
            # Keep reference to avoid garbage collection:
            self.buttons.append(button)


class IrReceiver:
    """Handles IR remote control input via LIRC."""

    def __init__(self, get_player, set_player, database_url, player_lock, reset_last_activity, socket_path='/var/run/lirc/lircd'):
        self.socket_path = socket_path
        self._running = False
        self._thread = None

        self.action_handler = PlayerActionHandler(
            get_player, set_player, database_url, player_lock, reset_last_activity
        )

        ir_keys = ['KEY_POWER', 'KEY_PAUSE', 'KEY_NEXT', 'KEY_PREVIOUS']
        self.key_map = map_actions(ir_keys)

    def _read_loop(self):
        try:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.connect(self.socket_path)
            sock_file = sock.makefile()
        except Exception as e:
            logging.error(f"Failed to connect to LIRC socket: {e}")
            return

        logging.info("Listening for IR input...")
        while self._running:
            try:
                line = sock_file.readline()
                if not line:
                    continue

                parts = line.strip().split()
                if len(parts) >= 3:
                    key = parts[2]
                    if key in self.key_map:
                        action = self.key_map[key]
                        logging.info(
                            f"Received key: {key} -> action: {action}")
                        self.action_handler.handle_action(action)
            except Exception as e:
                logging.error(f"Error reading from LIRC socket: {e}")
                break

        try:
            sock_file.close()
            sock.close()
        except:
            pass

    def start(self):
        if not os.path.exists(self.socket_path):
            raise FileNotFoundError(
                f"LIRC socket not found at {self.socket_path}")

        self._running = True
        self._thread = threading.Thread(target=self._read_loop, daemon=True)
        self._thread.start()
        logging.info("IR receiver started")

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join()
        logging.info("IR receiver stopped")


def create_button_handler(handler_type, get_player, set_player, database_url, player_lock, reset_last_activity):
    """Create the specified input handler type."""

    if handler_type == "gpio":
        if not Button:
            raise RuntimeError("gpiozero.Button is not available")
        return GpioButtonHandler(get_player, set_player, database_url, player_lock, reset_last_activity)

    elif handler_type == "ir":
        if not os.path.exists('/var/run/lirc/lircd'):
            raise FileNotFoundError(
                "LIRC socket not found at /var/run/lirc/lircd")
        handler = IrReceiver(get_player, set_player,
                             database_url, player_lock, reset_last_activity)
        handler.start()
        return handler

    else:
        raise ValueError(f"Unknown handler type: {handler_type}")


def map_actions(values):
    """
    Given a list of values, returns a dict mapping each value
    to the corresponding action by position.
    """
    actions = ["shutdown", "toggle_playback", "next_track", "previous_track", "volume_up", "volume_down"]

    if len(values) != len(actions):
        raise ValueError(f"Expected {len(actions)} values, got {len(values)}")

    return {value: action for value, action in zip(values, actions)}
