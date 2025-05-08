import logging
import os
import sqlite3
import threading
import time

import requests

import env as _
import utils

try:
    from gpiozero import Button
except ImportError:
    Button = None


import db_setup

try:
    import led
except ImportError:
    led = None

import register_rfid

from spotify import get_spotify_auth_token

if os.getenv("DEBUG") == "true":
    level = logging.DEBUG
else:
    level = logging.INFO

logging.basicConfig(
    level=level,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler("toem.log"),
        logging.StreamHandler()
    ]
)

player_lock = threading.Lock()
last_activity = time.time()
activity_lock = threading.Lock()


def reset_last_activity():
    global last_activity
    with activity_lock:
        last_activity = time.time()
        logging.debug("Last activity reset at %.0f", last_activity)


class ButtonHandler:
    def __init__(self, player=None):
        self.player_ready = False
        self.player = player
        self.last_button = None
        self.consecutive_presses = 0

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

    def handle_loading_led(self):
        while not self.player_ready:
            if led:
                led.toggle_led(23)
            time.sleep(0.1)

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
                    utils.shutdown(self.player)

        elif button == "toggle_playback":
            with player_lock:
                if self.player:
                    logging.info("Toggle playback button pressed.")
                    utils.play_sound(button)
                    if self.player.playback_started:
                        self.player.toggle_playback()
                    else:
                        self.player.play()
                else:
                    logging.warning(
                        "Player is not initialized. Cannot toggle playback.")
                    utils.play_sound("error")

        elif button == "next_track":
            with player_lock:
                if self.player:
                    logging.info("Next track button pressed.")
                    utils.play_sound(button)
                    self.player.next_track()
                else:
                    logging.warning(
                        "Player is not initialized. Cannot skip to next track.")
                    utils.play_sound("error")

        elif button == "previous_track":
            with player_lock:
                if self.player:
                    logging.info("Previous track button pressed.")
                    utils.play_sound(button)
                    self.player.previous_track()
                else:
                    logging.warning(
                        "Player is not initialized. Cannot skip to previous track.")
                    utils.play_sound("error")


def initialize_last_played(db, spotify_auth_token, database_url):
    """Attempt to initialize the last played player, handling Spotify/network issues."""
    last_played = utils.get_last_played(db)
    if not last_played:
        return None

    music_data = utils.get_music_data(db, last_played)
    if not music_data:
        return None

    player = None

    if music_data["source"] == "spotify":
        if spotify_auth_token:
            with player_lock:
                temp_player = utils.create_player(
                    spotify_auth_token, music_data, database_url)
                for _ in range(10):
                    if temp_player.check_device_status():
                        player = temp_player
                        break
                    time.sleep(0.5)
                else:
                    logging.warning(
                        "Spotify device not responding; skipping.")
        else:
            logging.warning(
                "Skipped Spotify player setup due to missing token.")
    else:
        with player_lock:
            player = utils.create_player(None, music_data, database_url)

    return player


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

    if Button:
        button_handler = ButtonHandler(None)

        # Start the LED blinking to indicate loading status
        threading.Thread(target=button_handler.handle_loading_led,
                         daemon=True).start()
    else:
        button_handler = None

    try:
        # check internet connection
        requests.head("https://api.spotify.com", timeout=1)
        spotify_auth_token = get_spotify_auth_token()
    except requests.RequestException:
        logging.warning(
            "No internet connection; skipping Spotify player setup.")
        spotify_auth_token = None

    player = initialize_last_played(db, spotify_auth_token, DATABASE_URL)

    if button_handler:
        button_handler.player = player
        button_handler.player_ready = True

    utils.play_sound("start")
    if led:
        led.turn_off_led(14)
        led.turn_on_led(23)

    reset_last_activity()

    # Shutdown timer
    def watchdog_loop():
        while True:
            with activity_lock:
                inactive_for = time.time() - last_activity
            if inactive_for > 3600:
                logging.info(
                    "Watchdog: system has been idle for %.0f seconds", inactive_for)
                utils.shutdown(player)
            time.sleep(5)

    threading.Thread(target=watchdog_loop, daemon=True).start()

    while True:
        # Wait for RFID input
        rfid = input("Enter RFID: ")
        reset_last_activity()

        if not rfid:
            break

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
                    if led:
                        led.flash_led(23)
                    if player:
                        player.pause_playback()
                        player.save_playback_state()

                    if music_data["source"] == "spotify":
                        if spotify_auth_token:
                            player = utils.create_player(
                                spotify_auth_token, music_data, DATABASE_URL)
                        else:
                            logging.warning(
                                "Cannot play Spotify track: missing auth token")
                            player = None
                    else:
                        player = utils.create_player(
                            None, music_data, DATABASE_URL)

                    if player:
                        player.play()
                        if button_handler:
                            button_handler.player = player

                    utils.save_last_played(db, music_data["rfid"])

                else:
                    logging.warning("Unknown RFID %s", rfid)
                    utils.play_sound("error")


if __name__ == "__main__":
    main()
