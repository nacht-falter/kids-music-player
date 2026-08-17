import logging
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


class ButtonHandler:
    # How long a shutdown confirmation stays armed. Without a timeout the first
    # press arms the device indefinitely, so an accidental press hours earlier
    # turns the next one into an immediate shutdown with no confirmation.
    SHUTDOWN_CONFIRM_TIMEOUT = 5

    def __init__(self, get_player, set_player, database_url, player_lock, reset_last_activity):
        if not Button:
            raise RuntimeError(
                "gpiozero.Button is not available. Cannot initialize ButtonHandler.")

        self.get_player = get_player
        self.set_player = set_player
        self.last_button = None
        self.consecutive_presses = 0
        # Guards the press counter only. Deliberately not player_lock: the
        # shutdown branch calls utils.shutdown() while holding that one.
        self.confirm_lock = threading.Lock()
        self.confirm_timer = None
        self.database_url = database_url
        self.player_lock = player_lock
        self.reset_last_activity = reset_last_activity

        if Button:
            # Set up buttons with callbacks
            self.button_3 = Button(3)
            self.button_3.when_pressed = lambda: self.handle_buttons(
                "shutdown")

            self.button_17 = Button(17)
            self.button_17.when_pressed = lambda: self.handle_buttons(
                "toggle_playback"
            )

            self.button_27 = Button(27)
            self.button_27.when_pressed = lambda: self.handle_buttons(
                "next_track")

            self.button_22 = Button(22)
            self.button_22.when_pressed = lambda: self.handle_buttons(
                "previous_track"
            )

    def _arm_confirmation_timeout(self):
        """Disarm the shutdown confirmation if no second press follows"""
        with self.confirm_lock:
            if self.confirm_timer:
                self.confirm_timer.cancel()
            self.confirm_timer = threading.Timer(
                self.SHUTDOWN_CONFIRM_TIMEOUT, self._expire_confirmation)
            self.confirm_timer.daemon = True
            self.confirm_timer.start()

    def _expire_confirmation(self):
        with self.confirm_lock:
            self.confirm_timer = None
            if self.last_button == "shutdown":
                logging.info(
                    "Shutdown confirmation expired without a second press.")
                self.last_button = None
                self.consecutive_presses = 0

    def _clear_confirmation(self):
        with self.confirm_lock:
            if self.confirm_timer:
                self.confirm_timer.cancel()
                self.confirm_timer = None
            self.last_button = None
            self.consecutive_presses = 0

    def handle_buttons(self, button):
        self.reset_last_activity()

        with self.confirm_lock:
            if self.last_button == button:
                self.consecutive_presses += 1
            else:
                self.last_button = button
                self.consecutive_presses = 1
            presses = self.consecutive_presses

        if button == "shutdown":
            with self.player_lock:
                # `>= 2` rather than `== 2`: a rapid third press used to match
                # neither branch, leaving the button dead until a different one
                # was pressed.
                if presses >= 2:
                    self._clear_confirmation()
                    logging.info("Shutdown confirmed. Shutting down.")
                    utils.shutdown(self.get_player())
                else:
                    logging.info(
                        "Shutdown button pressed once. Confirming shutdown.")
                    utils.play_sound("confirm_shutdown")
                    self._arm_confirmation_timeout()

        elif button == "toggle_playback":
            with self.player_lock:
                player = self.get_player()
                if player:
                    logging.info("Toggle playback button pressed.")
                    utils.play_sound(button)
                    if player.playback_started:
                        player.toggle_playback()
                    else:
                        player.play()
                else:
                    utils.play_sound(button)
                    self._create_and_play_last_player()

        elif button == "next_track":
            with self.player_lock:
                player = self.get_player()
                if player:
                    logging.info("Next track button pressed.")
                    utils.play_sound(button)
                    player.next_track()
                else:
                    logging.warning(
                        "Player is not initialized. Cannot skip to next track.")
                    utils.play_sound("error")

        elif button == "previous_track":
            with self.player_lock:
                player = self.get_player()
                if player:
                    logging.info("Previous track button pressed.")
                    utils.play_sound(button)
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
                    logging.warning(
                        "No last played data to create player.")
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
