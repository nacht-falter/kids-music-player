import logging
import os
import sqlite3
import threading
import time

import db_setup
import env as _
import utils

try:
    from gpiozero import Button
except ImportError:
    Button = None

try:
    import led
except ImportError:
    led = None

if os.getenv("DEBUG") == "true":
    level = logging.DEBUG
else:
    level = logging.INFO

app_dir = os.path.dirname(os.path.abspath(__file__))
log_path = os.path.join(app_dir, "toem.log")

logging.basicConfig(
    level=level,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(log_path),
        logging.StreamHandler()
    ]
)


player_lock = threading.Lock()
last_activity = time.monotonic()
activity_lock = threading.Lock()
IDLE_TIME = os.environ.get("IDLE_TIME")


def reset_last_activity():
    global last_activity
    with activity_lock:
        last_activity = time.monotonic()
        logging.debug("Last activity reset at %.0f", last_activity)


class ButtonHandler:
    def __init__(self, get_player, set_player, database_url):
        self.get_player = get_player
        self.set_player = set_player
        self.last_button = None
        self.consecutive_presses = 0
        self.database_url = database_url

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

    def handle_buttons(self, button):
        reset_last_activity()

        if self.last_button == button:
            self.consecutive_presses += 1
        else:
            self.last_button = button
            self.consecutive_presses = 1

        if button == "shutdown":
            with player_lock:
                if self.consecutive_presses == 1:
                    logging.info(
                        "Shutdown button pressed once. Confirming shutdown.")
                    utils.play_sound("confirm_shutdown")
                elif self.consecutive_presses == 2:
                    self.consecutive_presses = 0
                    logging.info("Shutdown confirmed. Shutting down.")
                    utils.shutdown(self.get_player())

        elif button == "toggle_playback":
            with player_lock:
                player = self.get_player()
                if player:
                    logging.info("Toggle playback button pressed.")
                    utils.play_sound(button)
                    if player.playback_started:
                        player.toggle_playback()
                    else:
                        player.play()
                else:
                    self._create_and_play_last_player()

        elif button == "next_track":
            with player_lock:
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
            with player_lock:
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
                    logging.warning("No last played data to create player.")
                    utils.play_sound("error")
                    return

                if led:
                    stop_event, thread = led.start_flashing(23)
                else:
                    stop_event, thread = None, None

                try:
                    new_player = utils.create_player(music_data, db)
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


def main():
    # Prepare database:
    DATABASE_URL = os.environ.get("DATABASE_URL")

    if not DATABASE_URL:
        logging.error("DATABASE_URL environment variable is not set.")
        raise ValueError("DATABASE_URL environment variable is required")

    logging.info("Connecting to database at %s", DATABASE_URL)

    if not os.path.exists(DATABASE_URL):
        db_setup.create_db(DATABASE_URL)
        logging.info("Database created at %s", DATABASE_URL)

    db = sqlite3.connect(DATABASE_URL)

    player = None

    # Getter and setter for ButtonHandler
    def get_player():
        return player

    def set_player(new_player):
        nonlocal player
        player = new_player

    button_handler = ButtonHandler(
        get_player, set_player, DATABASE_URL) if Button else None

    # Shutdown timer
    def watchdog_loop():
        while True:
            with activity_lock:
                inactive_for = time.monotonic() - last_activity
            if inactive_for > (int(IDLE_TIME) if IDLE_TIME else 3600):
                logging.info(
                    "Watchdog: system has been idle for %.0f seconds", inactive_for)
                utils.shutdown(player)
            time.sleep(5)

    threading.Thread(target=watchdog_loop, daemon=True).start()

    utils.play_sound("start")
    if led:
        led.turn_on_led(23)

    reset_last_activity()

    while True:
        # Wait for RFID input
        rfid = input("Enter RFID: ")
        reset_last_activity()

        logging.info("Scanned RFID: %s", rfid)

        with player_lock:
            # Check if RFID is already playing
            if player and rfid == player.rfid:
                utils.play_sound("confirm")
                utils.handle_already_playing(player)

            else:
                music_data = utils.get_music_data(db, rfid)

                if music_data:
                    utils.play_sound("confirm")
                    if player:
                        player.pause_playback()
                        player.save_playback_state()

                    if led:
                        stop_event, thread = led.start_flashing(23)
                    else:
                        stop_event, thread = None, None

                    try:
                        player = utils.create_player(music_data, db)
                    except Exception as e:
                        logging.exception("Failed to create player.")
                        utils.play_sound("playback_error")
                        player = None
                    finally:
                        if led and stop_event and thread:
                            led.stop_flashing(stop_event, thread)

                    if player:
                        player.play()
                        if button_handler:
                            button_handler.set_player(player)

                    utils.save_last_played(db, music_data["rfid"])

                else:
                    logging.warning("Unknown RFID %s", rfid)
                    utils.play_sound("error")


if __name__ == "__main__":
    main()
