import json
import logging
import os
import sqlite3
import time

import requests

from register_rfid import speak
class SpotifyPlayer:
    def __init__(self, spotify_auth_token, rfid, playback_state, location, database_url):
        self.base_url = "https://api.spotify.com/v1"
        self.spotify_auth_token = spotify_auth_token or get_spotify_auth_token()
        self.headers = {"Authorization": f"Bearer {self.spotify_auth_token}"}
        self.device_id = os.environ.get("DEVICE_ID")
        self.database_url = database_url
        self.rfid = rfid
        self.playback_state = (
            json.loads(playback_state)
            if playback_state
            else {"offset": {"position": 0}, "position_ms": 0}
        )
        self.location = location
        self.playing = False
        self.active_device = None
        self.playback_started = False
        logging.info("SpotifyPlayer initialized for RFID %s", rfid)

    def check_device_status(self):
        try:
            response = requests.get(
                f"{self.base_url}/me/player/devices", headers=self.headers)
            response.raise_for_status()
            for device in response.json().get("devices", []):
                if device.get("id") == self.device_id:
                    return True
        except requests.RequestException as e:
            self.handle_exception("Device status check failed", e)
        return False

    def check_playback_status(self):
        try:
            response = requests.get(
                f"{self.base_url}/me/player", headers=self.headers)
            if response.status_code == 204:
                self.active_device = None
                return None
            response.raise_for_status()
            playback = response.json()
            device_id = playback.get("device", {}).get("id")
            self.active_device = device_id
            self.playing = (device_id == self.device_id) and playback.get(
                "is_playing")
            return playback
        except requests.RequestException as e:
            self.handle_exception("Playback status check failed", e)
            return None

    def play(self, toggle=False):
        url = f"{self.base_url}/me/player/play?device_id={self.device_id}"
        data = {
            "context_uri": self.location,
            "offset": {"position": self.playback_state["offset"]["position"]},
            "position_ms": self.playback_state["position_ms"],
        }
        try:
            response = requests.put(
                url, headers=self.headers, json=None if toggle else data)
            response.raise_for_status()
            self.playing = True
            self.playback_started = True
            self.active_device = self.device_id
            logging.info("Playback started on device %s", self.device_id)
        except requests.RequestException as e:
            self.handle_exception("Playback failed", e)
            speak("I can't play this right now, sorry.")

    def pause_playback(self):
        if not self.playing:
            return
        url = f"{self.base_url}/me/player/pause?device_id={self.device_id}"
        try:
            response = requests.put(url, headers=self.headers)
            response.raise_for_status()
            self.playing = False
            self.active_device = None
            logging.info("Playback paused")
        except requests.RequestException as e:
            self.handle_exception("Pause failed", e)

    def toggle_playback(self):
        if self.playing:
            self.pause_playback()
        else:
            self.play(toggle=True)

    def next_track(self):
        if self.active_device != self.device_id:
            logging.warning(
                "Can't skip track: playback controlled by another device.")
            return
        url = f"{self.base_url}/me/player/next?device_id={self.device_id}"
        try:
            response = requests.post(url, headers=self.headers)
            response.raise_for_status()
            self.playing = True
            logging.info("Skipped to next track")
        except requests.RequestException as e:
            self.handle_exception("Next track failed", e)

    def previous_track(self):
        if self.active_device != self.device_id:
            logging.warning(
                "Can't go to previous track: playback controlled by another device.")
            return
        url = f"{self.base_url}/me/player/previous?device_id={self.device_id}"
        try:
            response = requests.post(url, headers=self.headers)
            response.raise_for_status()
            self.playing = True
            logging.info("Returned to previous track")
        except requests.RequestException as e:
            self.handle_exception("Previous track failed", e)

    def restart_playback(self):
        url = f"{self.base_url}/me/player/play?device_id={self.device_id}"
        data = {"context_uri": self.location,
                "offset": {"position": 0}, "position_ms": 0}
        try:
            response = requests.put(url, headers=self.headers, json=data)
            response.raise_for_status()
            self.playing = True
            logging.info("Playback restarted from beginning")
        except requests.RequestException as e:
            self.handle_exception("Restart failed", e)

    def save_playback_state(self):
        playback = self.check_playback_status()
        if playback:
            position_ms = playback.get("progress_ms", 0)
            track_number = playback.get("item", {}).get("track_number", 1)
            self.playback_state = {
                "offset": {"position": track_number - 1},
                "position_ms": position_ms,
            }
            try:
                with sqlite3.connect(self.database_url) as db:
                    db.cursor().execute(
                        "UPDATE music SET playback_state = ? WHERE rfid = ?",
                        (json.dumps(self.playback_state), self.rfid),
                    )
                logging.info("Playback state saved for RFID %s", self.rfid)
            except sqlite3.DatabaseError as e:
                self.handle_exception("Saving playback state failed", e)
        else:
            logging.debug("No playback data available to save")

    def handle_exception(self, message, e):
        logging.error("%s: %s", message, e, exc_info=True)


def get_spotify_auth_token():
    """Request and return Spotify auth token, with retry logic and error handling."""
    logging.debug("Requesting Spotify auth token...")
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

    retries = 3
    delay = 3

    for attempt in range(retries):
        try:
            response = requests.post(
                token_url, data=token_data, headers=token_headers)
            response.raise_for_status()
            logging.info("Successfully retrieved Spotify auth token.")
            return response.json()["access_token"]

        except requests.RequestException as e:
            logging.error("Auth token request failed (Attempt %d/%d): %s",
                          attempt + 1, retries, e, exc_info=True)
            if attempt < retries - 1:
                logging.info("Retrying in %d seconds...", delay)
                time.sleep(delay)
            else:
                logging.error(
                    "Exceeded maximum retries for Spotify auth token request.")
                return None
