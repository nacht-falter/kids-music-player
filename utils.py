import logging
import os
import subprocess

import env as _

try:
    import led
except ImportError:
    led = None


from local import AudioPlayer
from spotify import SpotifyPlayer


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


def create_player(music_data, db):
    """Create audio player instance"""
    logging.info("Creating player for RFID %s", music_data["rfid"])

    if music_data["source"] == "spotify":
        return SpotifyPlayer(
            music_data["rfid"],
            music_data["playback_state"],
            music_data["location"],
            db
        )
    elif music_data["source"] == "local":
        return AudioPlayer(
            music_data["rfid"],
            music_data["playback_state"],
            music_data["location"],
            db
        )
    else:
        logging.warning("Unknown music source: %s", music_data["source"])
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


def shutdown(player):
    """Shutdown computer"""
    logging.info("Initiating shutdown...")
    play_sound("shutdown")
    if player:
        player.pause_playback()
        player.save_playback_state()
    if led:
        led.turn_on_led(14)
        led.turn_off_led(23)
    if os.environ.get("DEVELOPMENT") == "False":
        logging.info("System is shutting down...")
        os.system("sudo shutdown -h now")
