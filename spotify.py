import json
import logging
import os
import sqlite3
import time

import requests

import utils


class SpotifyAuthManager:
    def __init__(self):
        self.token = None
        self.expiry = 0  # Timestamp when token expires
        self.usercreds = os.environ.get("USERCREDS")
        self.refresh_token = os.environ.get("REFRESH_TOKEN")

    def get_token(self):
        if not self.token or time.time() >= self.expiry:
            self._refresh_token()
        return self.token

    def _refresh_token(self):
        logging.debug("Requesting Spotify auth token...")
        token_url = "https://accounts.spotify.com/api/token"
        token_data = {
            "grant_type": "refresh_token",
            "refresh_token": self.refresh_token,
        }
        token_headers = {
            "Authorization": f"Basic {self.usercreds}",
        }

        retries = 3
        delay = 3

        for attempt in range(retries):
            try:
                response = requests.post(
                    token_url, data=token_data, headers=token_headers)
                response.raise_for_status()
                token_info = response.json()
                self.token = token_info["access_token"]
                expires_in = token_info.get("expires_in", 3600)
                self.expiry = time.time() + expires_in - 60  # Refresh 1 min before expiry
                logging.info("Successfully retrieved Spotify auth token.")
                return

            except requests.RequestException as e:
                logging.error("Auth token request failed (Attempt %d/%d): %s",
                              attempt + 1, retries, e, exc_info=True)
                if attempt < retries - 1:
                    logging.info("Retrying in %d seconds...", delay)
                    time.sleep(delay)
                else:
                    logging.error(
                        "Exceeded maximum retries for Spotify auth token request.")
                    self.token = None
                    self.expiry = 0


_auth_manager = None


def get_auth_manager():
    global _auth_manager
    if _auth_manager is None:
        _auth_manager = SpotifyAuthManager()
    return _auth_manager


class SpotifyPlayer:
    def __init__(self, rfid, playback_state, location, db):
        self.base_url = "https://api.spotify.com/v1"
        self.auth_manager = get_auth_manager()
        self.device_id = os.environ.get("DEVICE_ID")
        if not self.device_id:
            raise ValueError("DEVICE_ID environment variable is not set")
        self.db = db
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

    def _get_headers(self):
        token = self.auth_manager.get_token()
        return {"Authorization": f"Bearer {token}"}

    def _device_url(self, endpoint):
        return f"{self.base_url}/me/player/{endpoint}?device_id={self.device_id}"

    def is_ready(self):
        playback = self.check_playback_status()
        return playback is not None and self.active_device == self.device_id

    def check_playback_status(self):
        try:
            response = requests.get(
                f"{self.base_url}/me/player", headers=self._get_headers())
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

    def play(self):
        url = self._device_url("play")
        position = self.playback_state.get("offset", {}).get("position", 0)
        position_ms = self.playback_state.get("position_ms", 0)

        data = {
            "context_uri": self.location,
            "offset": {"position": position},
            "position_ms": position_ms,
        }

        try:
            response = requests.put(
                url, headers=self._get_headers(), json=data)
            response.raise_for_status()
            self.playing = True
            self.playback_started = True
            self.active_device = self.device_id
            logging.info(
                "Started playback from beginning at position %d (%d ms)", position, position_ms)
        except requests.RequestException as e:
            self.handle_exception("Playback failed", e)
            utils.play_sound("playback_error")

    def resume_playback(self):
        url = self._device_url("play")
        try:
            response = requests.put(url, headers=self._get_headers(), json={})
            response.raise_for_status()
            self.playing = True
            self.active_device = self.device_id
            logging.info("Resumed playback on device %s", self.device_id)
        except requests.RequestException as e:
            self.handle_exception("Resuming playback failed", e)

    def pause_playback(self):
        if not self.playing:
            return
        url = self._device_url("pause")
        try:
            response = requests.put(url, headers=self._get_headers())
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
            self.resume_playback()

    def next_track(self):
        if self.active_device != self.device_id:
            logging.warning(
                "Can't skip track: playback controlled by another device.")
            return
        url = self._device_url("next")
        try:
            response = requests.post(url, headers=self._get_headers())
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
        url = self._device_url("previous")
        try:
            response = requests.post(url, headers=self._get_headers())
            response.raise_for_status()
            self.playing = True
            logging.info("Returned to previous track")
        except requests.RequestException as e:
            self.handle_exception("Previous track failed", e)

    def restart_playback(self):
        url = self._device_url("play")
        data = {"context_uri": self.location,
                "offset": {"position": 0}, "position_ms": 0}
        try:
            response = requests.put(
                url, headers=self._get_headers(), json=data)
            response.raise_for_status()
            self.playing = True
            logging.info("Playback restarted from beginning")
        except requests.RequestException as e:
            self.handle_exception("Restart failed", e)

    def save_playback_state(self):
        playback = self.check_playback_status()
        if not playback:
            logging.debug("No playback data available to save")
            return

        position_ms = playback.get("progress_ms", 0)
        item = playback.get("item", {})
        context_uri = playback.get("context", {}).get("uri")
        track_uri = item.get("uri")
        offset_position = 0  # fallback default

        if not context_uri or not track_uri:
            logging.warning(
                "Missing context or track URI; can't determine position")
        else:
            try:
                context_parts = context_uri.split(":")
                if len(context_parts) == 3 and context_parts[1] == "playlist":
                    offset_position = self._get_track_position_in_playlist(
                        context_parts[2], track_uri)
                elif len(context_parts) == 3 and context_parts[1] == "album":
                    offset_position = max(0, item.get("track_number", 1) - 1)
                else:
                    logging.warning(
                        "Unsupported context type: %s", context_uri)
            except Exception as e:
                self.handle_exception("Failed to resolve track position", e)

        self.playback_state = {
            "offset": {"position": offset_position},
            "position_ms": position_ms,
        }

        try:
            self.db.cursor().execute(
                "UPDATE music SET playback_state = ? WHERE rfid = ?",
                (json.dumps(self.playback_state), self.rfid),
            )
            logging.info("Playback state saved for RFID %s", self.rfid)
        except sqlite3.DatabaseError as e:
            self.handle_exception("Saving playback state failed", e)

    def _get_track_position_in_playlist(self, playlist_id, track_uri):
        headers = self._get_headers()
        url = f"{self.base_url}/playlists/{playlist_id}/tracks"
        params = {"limit": 100, "offset": 0}

        while True:
            response = requests.get(url, headers=headers, params=params)
            response.raise_for_status()
            items = response.json().get("items", [])
            for index, item in enumerate(items):
                track = item.get("track", {})
                if track.get("uri") == track_uri:
                    return index
            if response.json().get("next"):
                params["offset"] += params["limit"]
            else:
                break

        logging.warning("Track URI not found in playlist")
        return 0  # fallback

    def handle_exception(self, message, e):
        logging.error("%s: %s", message, e, exc_info=True)
