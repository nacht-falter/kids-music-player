import requests
import os
import json
import sqlite3
from playsound import playsound


class SpotifyPlayer:
    def __init__(self, auth_token, rfid, playback_state, location):
        self.base_url = "https://api.spotify.com/v1"
        self.headers = {"Authorization": f"Bearer {auth_token}"}
        self.device_id = os.environ.get("DEVICE_ID")
        self.database_url = os.environ.get("DATABASE_URL")
        self.rfid = rfid
        self.playback_state = (
            json.loads(playback_state)
            if playback_state
            else {"offset": 0, "position_ms": 0}
        )
        self.location = location
        self.playing = False
        print("Spotify player initialized.")

    def check_playback_status(self):
        print("Checking spotify playback status...")
        request_url = self.base_url + "/me/player"

        try:
            response = requests.get(request_url, headers=self.headers)

            if response.status_code == 204:
                print("No content currently playing.")
                return None

            response.raise_for_status()

            if response.json().get("device").get("id") == self.device_id:
                self.playing = response.json().get("is_playing")
            return response.json()

        except requests.RequestException as e:
            self.handle_exception("Failed to get playback status:", e)
            return None

    def play(self, toggle=False):
        print("Playing...")
        request_url = (
            self.base_url + "/me/player/play?device_id=" + self.device_id
        )
        data = {
            "context_uri": self.location,
            "offset": self.playback_state.get("offset"),
            "position_ms": self.playback_state.get("position_ms"),
        }

        try:
            if toggle:  # make request without data
                response = requests.put(request_url, headers=self.headers)
            else:
                response = requests.put(
                    request_url, headers=self.headers, json=data
                )
            response.raise_for_status()
            self.playing = True

        except requests.RequestException as e:
            self.handle_exception("Failed to play:", e)

    def pause_playback(self):
        print("Pausing playback...")
        request_url = (
            self.base_url + "/me/player/pause?device_id=" + self.device_id
        )

        if self.playing:
            try:
                response = requests.put(request_url, headers=self.headers)
                response.raise_for_status()
                self.playing = False

            except requests.RequestException as e:
                self.handle_exception("Failed to pause playback:", e)

    def toggle_playback(self):
        print("Toggling playback...")
        if self.playing:
            self.pause_playback()
        else:
            self.play(toggle=True)

    def next_track(self):
        print("Next track...")
        request_url = (
            self.base_url + "/me/player/next?device_id=" + self.device_id
        )

        try:
            response = requests.post(request_url, headers=self.headers)
            response.raise_for_status()
            self.playing = True
        except requests.RequestException as e:
            self.handle_exception("Next track failed:", e)

    def previous_track(self):
        print("Previous track...")
        request_url = (
            self.base_url + "/me/player/previous?device_id=" + self.device_id
        )

        try:
            response = requests.post(request_url, headers=self.headers)
            response.raise_for_status()
            self.playing = True

        except requests.RequestException as e:
            self.handle_exception("Previous track failed:", e)

    def restart_playback(self):
        print("Restart playback...")
        request_url = (
            self.base_url + "/me/player/play?device_id=" + self.device_id
        )
        data = {
            "context_uri": self.location,
            "offset": 0,
            "position_ms": 0,
        }

        try:
            response = requests.put(
                request_url, headers=self.headers, json=data
            )
            response.raise_for_status()
            self.playing = True

        except requests.RequestException as e:
            self.handle_exception("Restart playback failed:", e)

    def save_playback_state(self):
        print("Saving playback state...")
        playback_state = self.check_playback_status()
        if playback_state:
            position_ms = playback_state.get("progress_ms")
            track_number = playback_state.get("item").get("track_number")
            self.playback_state = {
                "offset": track_number - 1,
                "position_ms": position_ms,
            }
            with sqlite3.connect(self.database_url) as db:
                db.cursor().execute(
                    "UPDATE music SET playback_state = ? WHERE rfid = ?",
                    (json.dumps(self.playback_state), self.rfid),
                )
        else:
            print("No playback state to save.")

    def handle_exception(self, message, e):
        playsound("sounds/error.wav")
        print(f"{message}: {e})")


class SpotifyPlayer:
    def __init__(self, rfid, playback_state, location):
        self.base_url = "https://api.spotify.com/v1"
        self.headers = {"Authorization": f"Bearer {self.spotify_auth()}"}
        self.device_id = os.environ.get("DEVICE_ID")
        self.database_url = os.environ.get("DATABASE_URL")
        self.rfid = rfid
        self.playback_state = (
            json.loads(playback_state)
            if playback_state
            else {"offset": 0, "position_ms": 0}
        )
        self.location = location
        self.playing = False
        print("Spotify player initialized.")


def get_spotify_auth_token():
    print("Getting Spotify auth token...")
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
        response.raise_for_status()
        return response.json()["access_token"]
    except requests.RequestException as e:
        print("Failed to get Spotify auth token:", e)
        return None
