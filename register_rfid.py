import os
import gtts
from playsound import playsound
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
    response = requests.post(token_url, data=token_data, headers=token_headers)
    token_response_data = response.json()
    if response.status_code == 200:
        print("Token refreshed")
        return token_response_data["access_token"]
    else:
        print(response.status_code)
        return None


def get_spotify_uri(base_url, headers):
    request_url = base_url + "/me/player/currently-playing"
    response = requests.get(request_url, headers=headers)
    if response.status_code == 200:
        return response.json()["context"]["uri"]
    else:
        print(response.status_code)
        return None


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


def speak(text):
    gtts.gTTS(text=text).save("sounds/speech/instructions.mp3")
    playsound("sounds/speech/instructions.mp3")


def create_database_entry(db, rfid, table, value):
    cursor = db.cursor()
    if check_if_rfid_exists(db, rfid) is False:
        if table == "commands":
            cursor.execute(
                "INSERT INTO commands (rfid, command) VALUES (?, ?)",
                (rfid, value),
            )
            speak("RFID successfully registered.")
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
    create_database_entry(db, rfid, "music", uri)


def register_commands(db):
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