import requests
import os
import json
import sqlite3
import env


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

    def spotify_auth(self):
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
        response = requests.post(
            token_url, data=token_data, headers=token_headers
        )
        token_response_data = response.json()
        if response.status_code == 200:
            print("Token refreshed")
            return token_response_data["access_token"]
        else:
            print(response.status_code)
            return None

    def check_playback_status(self):
        request_url = self.base_url + "/me/player/currently-playing"
        response = requests.get(request_url, headers=self.headers)

        if response.status_code == 200:
            return response.json()
        else:
            print(response.status_code)
            return None

    def play(self):
        request_url = (
            self.base_url + "/me/player/play?device_id=" + self.device_id
        )
        data = {
            "context_uri": self.location,
            "offset": {"position": self.playback_state["offset"]},
            "position_ms": self.playback_state["position_ms"],
        }
        response = requests.put(request_url, headers=self.headers, json=data)
        self.playing = True

        if response.status_code != 204:
            print(response.status_code)

    def toggle_playback(self):
        if self.check_playback_status().get("is_playing"):
            request_url = (
                self.base_url + "/me/player/pause?device_id=" + self.device_id
            )
        else:
            request_url = (
                self.base_url + "/me/player/play?device_id=" + self.device_id
            )
        response = requests.put(request_url, headers=self.headers)
        self.playing = not self.playing

        if response.status_code != 204:
            print(response.status_code)

    def pause_playback(self):
        request_url = (
            self.base_url + "/me/player/pause?device_id=" + self.device_id
        )
        response = requests.put(request_url, headers=self.headers)
        self.playing = False

        if response.status_code != 204:
            print(response.status_code)

    def next_track(self):
        request_url = (
            self.base_url + "/me/player/next?device_id=" + self.device_id
        )
        response = requests.post(request_url, headers=self.headers)

        if response.status_code != 204:
            print(response.status_code)

    def previous_track(self):
        """Play previous track"""
        request_url = (
            self.base_url + "/me/player/previous?device_id=" + self.device_id
        )
        response = requests.post(request_url, headers=self.headers)
        if response.status_code != 204:
            print(response.status_code)

    def restart_playback(self):
        request_url = (
            self.base_url + "/me/player/play?device_id=" + self.device_id
        )
        data = {
            "context_uri": self.location,
            "offset": {"position": 0},
            "position_ms": 0,
        }
        response = requests.put(request_url, headers=self.headers, json=data)
        self.playing = True

        if response.status_code != 204:
            print(response.status_code)

    def save_playback_state(self):
        playback_state = self.check_playback_status()
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
