import logging
import os
import sqlite3
import sys
import threading
import time

from dotenv import load_dotenv

import db_setup
import utils
from buttons import ButtonHandler
from remote_sync import sync_db
from rfid import RfidReader

try:
    import led
except ImportError:
    led = None


class RFIDMusicPlayer:
    """Main application class for the RFID Music Player."""

    def __init__(self):
        self.player = None
        self.player_lock = threading.Lock()
        self.last_activity = time.monotonic()
        self.activity_lock = threading.Lock()
        self.sync_done = threading.Event()
        self.db = None
        self.database_url = None
        self.rfid_reader = None
        self.button_handler = None

    def initialize(self):
        """Initialize the application configuration and logging."""
        load_dotenv()

        try:
            utils.verify_env_file(os.environ)
        except Exception as e:
            print(f"Config error: {e}")
            return False

        if os.getenv("DEVELOPMENT", "").lower() == "true":
            level = logging.DEBUG
        else:
            level = logging.INFO

        app_dir = os.path.dirname(os.path.abspath(__file__))
        app_name = os.getenv("APP_NAME", "rfid_music_player").lower()
        log_path = os.path.join(app_dir, f"{app_name}.log")

        logging.basicConfig(
            level=level,
            format='%(asctime)s [%(levelname)s] %(message)s',
            handlers=[
                logging.FileHandler(log_path),
                logging.StreamHandler()
            ]
        )

        idle_time_env = os.environ.get("IDLE_TIME")
        self.idle_time = int(idle_time_env) if idle_time_env else 3600

        return True

    def setup_database(self):
        """Setup and connect to the database."""
        self.database_url = os.environ.get("DATABASE_URL")

        if not self.database_url:
            logging.error("DATABASE_URL environment variable is not set.")
            raise ValueError("DATABASE_URL environment variable is required.")

        if not os.path.exists(self.database_url):
            db_setup.create_db(self.database_url)
            logging.info("Database created at %s", self.database_url)

        self.db = sqlite3.connect(self.database_url)
        logging.info("Connected to database: %s", self.database_url)

    def setup_sync(self):
        """Setup database synchronization if enabled."""
        sync_enabled = os.environ.get("ENABLE_SYNC", "").lower() == "true"

        if sync_enabled:
            sync_thread = threading.Thread(
                target=sync_db,
                args=(self.database_url, self.sync_done),
                daemon=True
            )
            sync_thread.start()
            logging.info("Database sync enabled")
        else:
            logging.info("Sync disabled.")

    def setup_hardware(self):
        """Setup hardware components (buttons, RFID reader)."""
        try:
            self.button_handler = ButtonHandler(
                self.get_player,
                self.set_player,
                self.database_url,
                self.player_lock,
                self.reset_last_activity
            )
        except RuntimeError as e:
            logging.warning(e)

        self.rfid_reader = RfidReader()

    def get_player(self):
        """Get the current player instance."""
        return self.player

    def set_player(self, new_player):
        """Set the current player instance."""
        self.player = new_player

    def reset_last_activity(self):
        """Reset the last activity timestamp."""
        with self.activity_lock:
            self.last_activity = time.monotonic()
            logging.debug("Last activity reset at %.0f", self.last_activity)

    def start_watchdog(self):
        """Start the idle watchdog timer."""
        def watchdog_loop():
            while True:
                with self.activity_lock:
                    inactive_for = time.monotonic() - self.last_activity
                if inactive_for > self.idle_time:
                    logging.info(
                        "Watchdog: system has been idle for %.0f seconds",
                        inactive_for
                    )
                    utils.shutdown(self.player)
                time.sleep(1)

        threading.Thread(target=watchdog_loop, daemon=True).start()

    def handle_rfid_scan(self, rfid):
        """Handle an RFID scan event."""
        logging.info("Scanned RFID: %s", rfid)

        with self.player_lock:
            # Check if RFID is already playing
            if self.player and rfid == self.player.rfid:
                utils.play_sound("confirm")
                utils.handle_already_playing(self.player)
            else:
                music_data = utils.get_music_data(self.db, rfid)

                if music_data:
                    utils.play_sound("confirm")

                    if self.player:
                        self.player.pause_playback()
                        self.player.save_playback_state()

                    # Start LED flashing
                    if led:
                        stop_event, thread = led.start_flashing(23, 0)
                    else:
                        stop_event, thread = None, None

                    try:
                        self.player = utils.create_player(music_data, self.db)
                    except Exception as e:
                        logging.exception(
                            f"Failed to create player. Error: {e}")
                        utils.play_sound("playback_error")
                        self.player = None
                    finally:
                        if self.player:
                            self.player.play()
                            if self.button_handler:
                                self.button_handler.set_player(self.player)

                        utils.save_last_played(self.db, music_data["rfid"])

                        # Stop LED flashing
                        if led and stop_event and thread:
                            led.stop_flashing(stop_event, thread)
                else:
                    logging.warning("Unknown RFID %s", rfid)
                    utils.play_sound("error")

    def run(self):
        """Main application loop."""
        try:
            if not self.initialize():
                return 1

            self.setup_database()
            self.setup_sync()
            self.setup_hardware()

            self.start_watchdog()

            utils.play_sound("start")
            if led:
                led.turn_on_led(23)

            self.reset_last_activity()

            while True:
                # Wait for RFID input
                if self.rfid_reader:
                    rfid = self.rfid_reader.read_code()
                else:
                    logging.error("RFID reader not initialized")
                    break
                self.reset_last_activity()
                self.handle_rfid_scan(rfid)

        except KeyboardInterrupt:
            logging.info("Application interrupted by user")
        except Exception as e:
            logging.exception(f"Unexpected error: {e}")
            return 1
        finally:
            self.cleanup()

        return 0

    def cleanup(self):
        """Clean up resources."""
        if self.rfid_reader:
            self.rfid_reader.close()
        if self.db:
            self.db.close()


def main():
    """Entry point for the application."""
    app = RFIDMusicPlayer()
    return app.run()


if __name__ == "__main__":
    sys.exit(main())
