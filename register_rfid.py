import logging
import os
import sys

import gtts
import pygame
import requests


def spotify_auth():
    usercreds = os.environ.get("USERCREDS")
    refresh_token = os.environ.get("REFRESH_TOKEN")
    token_url = "https://accounts.spotify.com/api/token"
    token_data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
    }
    token_headers = {
        "Authorization": f"Basic {usercreds}",
    }

    try:
        response = requests.post(
            token_url, data=token_data, headers=token_headers
        )
        token_response_data = response.json()
        response.raise_for_status()
        logging.info("Spotify auth token refreshed")
        return token_response_data["access_token"]

    except requests.RequestException as e:
        handle_exception("Failed to get Spotify auth token:", e)


def get_spotify_uri(base_url, headers):
    request_url = base_url + "/me/player/currently-playing"

    try:
        response = requests.get(request_url, headers=headers)
        response.raise_for_status()
        return response.json()["context"]["uri"]

    except requests.RequestException as e:
        handle_exception("Failed to get playback status:", e)


def get_rfid():
    return input("Scan RFID: ")


def check_if_rfid_exists(db, rfid):
    cursor = db.cursor()
    rfid_music = cursor.execute(
        "SELECT * FROM music WHERE rfid=?", (rfid,)
    ).fetchone()
    rfid_commands = cursor.execute(
        "SELECT * FROM commands WHERE rfid=?", (rfid,)
    ).fetchone()

    if rfid_music or rfid_commands:
        speak(
            "This RFID is already registered. Please rescan the register "
            "tag and try again."
        )
        return True
    else:
        return False


def create_database_entry(db, rfid, table, value):
    cursor = db.cursor()
    if table == "commands":
        cursor.execute(
            "INSERT INTO commands (rfid, command) VALUES (?, ?)",
            (rfid, value),
        )
    elif table == "music":
        cursor.execute(
            "INSERT INTO music (rfid, source, location) VALUES (?, ?, ?)",
            (rfid, "spotify", value),
        )
    db.commit()


def register_spotify_rfid(db):
    base_url = "https://api.spotify.com/v1"
    headers = {"Authorization": f"Bearer {spotify_auth()}"}

    speak(
        "Please start playing a Spotify album on another device. "
        "Then scan the RFID you want to register."
    )

    rfid = get_rfid()
    uri = get_spotify_uri(base_url, headers)
    if check_if_rfid_exists(db, rfid) is False:
        create_database_entry(db, rfid, "music", uri)
        logging.info("RFID %s successfully registered.", rfid)
        speak("RFID successfully registered.")


def register_commands(db):
    logging.info("Registering commands...")
    if not db.cursor().execute("SELECT * FROM commands").fetchall():
        commands = {}
        speak("Please scan your RFID for toggling playback")
        commands["toggle_playback"] = get_rfid()
        speak("Please scan your RFID for next track")
        commands["next_track"] = get_rfid()
        speak("Please scan your RFID for previous track")
        commands["previous_track"] = get_rfid()
        speak("Please scan your RFID for shutdown")
        commands["shutdown"] = get_rfid()
        speak("Please scan your RFID for registering new RFIDs")
        commands["register_rfid"] = get_rfid()

        for command, rfid in commands.items():
            create_database_entry(db, rfid, "commands", command)

        speak("RFIDs successfully registered.")


def handle_exception(message, e):
    from main import play_sound
    play_sound("error")
    logging.error("%s: %s", message, e, exc_info=True)

    sys.exit(1)


def speak(text):
    logging.info("Speaking: %s", text)

    sound_folder = os.path.join(os.path.dirname(
        os.path.abspath(__file__)), "sounds", "speech")
    os.makedirs(sound_folder, exist_ok=True)  # Ensure directory exists
    file_path = os.path.join(sound_folder, "instructions.mp3")
    if os.path.exists(file_path):
        os.remove(file_path)

    tts = gtts.gTTS(text=text)
    tts.save(file_path)

    pygame.mixer.init()
    pygame.mixer.music.load(file_path)
    pygame.mixer.music.play()

    # Wait until playback finishes
    while pygame.mixer.music.get_busy():
        pygame.time.Clock().tick(10)
