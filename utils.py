import logging
import os
import subprocess
import time

from local import AudioPlayer
from spotify import SpotifyPlayer

try:
    import led
except ImportError:
    led = None


def get_music_data(db, rfid):
    """Get music data from database"""
    cursor = db.cursor()
    cursor.execute("SELECT * FROM music WHERE rfid = ?", (rfid,))
    result = cursor.fetchone()
    if result:
        columns = [desc[0] for desc in cursor.description]
        data = dict(zip(columns, result))
        logging.debug("Found music data for RFID %s: %s", rfid, data)
        return data
    else:
        logging.debug("No music data found for RFID %s", rfid)
        return None


def create_player(music_data, db, retries=10, delay=1):
    """Create audio player instance"""
    rfid = music_data["rfid"]
    source = music_data.get("source")
    playback_state = music_data.get("playback_state")
    location = music_data.get("location")

    logging.info("Creating player for RFID %s", rfid)

    if source == "spotify":
        for attempt in range(retries):
            player = SpotifyPlayer(rfid, playback_state, location, db)
            player.transfer_playback(play=False)
            if player.is_ready():
                logging.info(
                    "Spotify player ready after %d attempt(s)", attempt + 1)
                return player
            logging.warning("Spotify player not ready (attempt %d/%d), retrying in %d seconds...",
                            attempt + 1, retries, delay)
            time.sleep(delay)

        logging.error(
            "Failed to initialize a ready Spotify player after %d attempts", retries)
        return None

    elif source == "local":
        return AudioPlayer(rfid, playback_state, location, db)

    else:
        logging.warning("Unknown music source: %s", source)
        return None


def play_sound(event):
    """Play sound file associated with event"""
    sounds = {
        "start": "start",
        "confirm": "confirm",
        "error": "error",
        "next_track": "click",
        "previous_track": "click",
        "toggle_playback": "click",
        "confirm_shutdown": "confirm_shutdown",
        "shutdown": "shutdown",
        "playback_error": "playback_error"
    }

    if event not in sounds:
        raise ValueError(f"Sound file for event '{event}' not found.")

    sound_folder = os.path.join(os.path.dirname(
        os.path.abspath(__file__)), "sounds")
    file_path = os.path.join(sound_folder, f"{sounds[event]}.wav")

    try:
        subprocess.Popen(
            ["aplay", file_path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
    except FileNotFoundError:
        logging.error("aplay is not installed or not found in PATH.")
    except Exception as e:
        logging.exception(f"Failed to play audio file {file_path}: {e}")


def save_last_played(db, rfid):
    """Save last played album to database"""
    cursor = db.cursor()
    cursor.execute("DELETE FROM last_played")
    cursor.execute(
        "INSERT INTO last_played (last_played_rfid) VALUES (?)", (rfid,)
    )
    db.commit()
    logging.info("Last played RFID saved to database: %s", rfid)


def get_last_played_rfid(db):
    """Get last played album from database"""
    cursor = db.cursor()
    cursor.execute("SELECT last_played_rfid FROM last_played")
    result = cursor.fetchone()
    if result:
        logging.info("Last played album: %s", result[0])
        return result[0]
    else:
        logging.info("No last played album found.")
        return None


def handle_already_playing(player):
    """Handle already playing album"""
    if player.playing:
        logging.info("Already playing. Restarting playback.")
        player.restart_playback()
    else:
        logging.info("Playback is paused. Toggling playback.")
        player.toggle_playback()


def shutdown(player, sync_done=None):
    """Shutdown computer"""
    play_sound("shutdown")
    if player:
        player.pause_playback()
        player.save_playback_state()

    if led:
        led.turn_off_led(23)

    if sync_done and not sync_done.is_set():
        logging.info("Waiting for sync to complete...")
        sync_done.wait(timeout=10)

    logging.info("Shutting down... ")
    logging.shutdown()

    if os.getenv("DEVELOPMENT", "").lower() == "true":
        os._exit(os.EX_OK)
    else:
        os.system("sudo shutdown -h now")


def verify_env_file(config):
    if not config:
        raise ValueError(".env file is missing or empty.")

    required = [
        "SPOTIFY_USERCREDS",
        "SPOTIFY_REFRESH_TOKEN",
        "SPOTIFY_DEVICE_ID",
        "DATABASE_URL",
        "RFID_READER"
    ]

    missing = [k for k in required if not config.get(k)]
    if missing:
        raise ValueError(
            f"Missing required environment variable: {', '.join(missing)}")

    if config.get("ENABLE_SYNC", "").lower() == "true":
        if not config.get("SYNC_API_URL") or not config.get("SYNC_API_TOKEN"):
            raise ValueError(
                "ENABLE_SYNC is true but SYNC_API_URL or SYNC_API_TOKEN is missing.")
