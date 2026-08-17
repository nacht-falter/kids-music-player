import json
import logging
import os
import sqlite3
import time

import requests

import utils


class SpotifyAuthError(requests.RequestException):
    """Raised when no usable access token is available

    Subclasses RequestException so the existing `except requests.RequestException`
    handlers treat it like any other API failure. Raising something they do not
    catch would propagate to main.run(), which returns 1, and systemd's
    StartLimitBurst would then park the unit in a failed state after five
    restarts - turning an expired token into a device that stays dead.
    """


class SpotifyAuthManager:
    # Refreshing is only worth retrying for transient failures. These are
    # class attributes so tests can shrink them.
    RETRIES = 3
    RETRY_DELAY = 3
    # After Spotify rejects the credentials themselves, stop asking for a
    # while. Every API call goes through get_token(), so without this a dead
    # refresh token means a token request per call - ten per card scan, given
    # create_player's retries - all of them certain to fail.
    PERMANENT_FAILURE_COOLDOWN = 300

    def __init__(self):
        self.token = None
        self.expiry = 0  # Timestamp when token expires
        self.rejected_at = 0  # When Spotify last rejected our credentials
        self.usercreds = os.environ.get("SPOTIFY_USERCREDS")
        self.refresh_token = os.environ.get("SPOTIFY_REFRESH_TOKEN")

        if not self.usercreds:
            raise ValueError("SPOTIFY_USERCREDS environment variable is not set")

        if not self.refresh_token:
            raise ValueError("SPOTIFY_REFRESH_TOKEN environment variable is not set")

    def get_token(self):
        if not self.token or time.time() >= self.expiry:
            since_rejected = time.time() - self.rejected_at
            if self.rejected_at and since_rejected < self.PERMANENT_FAILURE_COOLDOWN:
                logging.debug(
                    "Skipping token request: credentials were rejected %.0fs "
                    "ago, retrying in %.0fs",
                    since_rejected, self.PERMANENT_FAILURE_COOLDOWN - since_rejected)
                return None
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

        retries = self.RETRIES
        delay = self.RETRY_DELAY

        for attempt in range(retries):
            try:
                response = requests.post(
                    token_url, data=token_data, headers=token_headers)

                # The reason lives in the body, not the status line. Without
                # this the 2026-08-16 outage showed only "400 Bad Request"
                # while the body said the refresh token had been revoked.
                if response.status_code >= 400:
                    logging.error(
                        "Auth token request failed: HTTP %d, body: %s",
                        response.status_code, response.text.strip())

                    if self._is_permanent_failure(response):
                        logging.error(
                            "Spotify rejected the refresh token itself, so "
                            "retrying cannot help. Re-authorize the app and "
                            "update SPOTIFY_REFRESH_TOKEN. Note Spotify "
                            "expires refresh tokens 6 months after "
                            "authorization.")
                        self.token = None
                        self.expiry = 0
                        self.rejected_at = time.time()
                        return

                response.raise_for_status()
                token_info = response.json()
                self.token = token_info["access_token"]
                expires_in = token_info.get("expires_in", 3600)
                self.expiry = time.time() + expires_in - 60  # Refresh 1 min before expiry
                self.rejected_at = 0
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

    @staticmethod
    def _is_permanent_failure(response):
        """Whether Spotify rejected the credentials rather than glitching

        invalid_grant means the refresh token is expired or revoked;
        invalid_client means the client id/secret are wrong. Neither is fixed
        by trying again.
        """
        try:
            error = (response.json() or {}).get("error")
        except ValueError:
            return False
        # The error field is a string on this endpoint, but be defensive.
        if isinstance(error, dict):
            error = error.get("message")
        return error in ("invalid_grant", "invalid_client")


_auth_manager = None


def get_auth_manager():
    global _auth_manager
    if _auth_manager is None:
        _auth_manager = SpotifyAuthManager()
    return _auth_manager


class SpotifyPlayer:
    def __init__(self, rfid, playback_state, location):
        self.base_url = "https://api.spotify.com/v1"
        self.auth_manager = get_auth_manager()
        self.device_id = os.environ.get("SPOTIFY_DEVICE_ID")
        if not self.device_id:
            raise ValueError("SPOTIFY_DEVICE_ID environment variable is not set")
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
        if not token:
            # Previously this sent "Bearer None" and let Spotify reject it,
            # which buried the real cause under a 401 from whichever call
            # happened to be next.
            raise SpotifyAuthError(
                "No Spotify access token available; re-authorization needed")
        return {"Authorization": f"Bearer {token}"}

    def _device_url(self, endpoint):
        return f"{self.base_url}/me/player/{endpoint}?device_id={self.device_id}"

    def transfer_playback(self, play=False):
        url = f"{self.base_url}/me/player"
        data = {"device_ids": [self.device_id], "play": play}
        try:
            response = requests.put(
                url, headers=self._get_headers(), json=data)
            response.raise_for_status()
            logging.info("Playback transferred to device %s", self.device_id)
            return True
        except requests.RequestException as e:
            self.handle_exception("Transfer playback failed", e)
            return False

    def is_ready(self):
        playback = self.check_playback_status()
        return playback is not None and self.active_device == self.device_id

    def owns_playback(self, playback):
        """Whether the current playback is this device playing our own album

        False when another device holds the session, or when this device was
        handed something else (e.g. a podcast transferred from a phone).
        """
        if not playback:
            return False
        device_id = (playback.get("device") or {}).get("id")
        context_uri = (playback.get("context") or {}).get("uri")
        return device_id == self.device_id and context_uri == self.location

    def _state_from_playback(self, playback):
        """Cheap position snapshot taken from a playback payload

        Unlike save_playback_state() this never resolves playlist positions,
        which would cost an extra API call every time it runs. For playlist
        contexts the last known offset is kept and only the elapsed time is
        updated; save_playback_state() resolves those properly.
        """
        item = playback.get("item") or {}
        context_uri = (playback.get("context") or {}).get("uri")
        context_parts = context_uri.split(":") if context_uri else []

        if (len(context_parts) == 3 and context_parts[1] == "album"
                and (item.get("disc_number") or 1) <= 1):
            offset_position = max(0, item.get("track_number", 1) - 1)
        else:
            # Playlists and multi-disc albums cannot be resolved without
            # another request, which is not worth making on every tick. Keep
            # the last known offset; save_playback_state() resolves it
            # properly.
            offset_position = (
                self.playback_state.get("offset") or {}).get("position", 0)

        return {
            "offset": {"position": offset_position},
            "position_ms": playback.get("progress_ms", 0),
        }

    def refresh_playback_state(self):
        """Record where our album has got to, while we can still see it

        Called periodically during playback. Without it self.playback_state
        only ever changes on a card switch or shutdown, so reclaiming after
        another device interrupts us would rewind to that stale position.
        Once the interrupting device holds the session our position is gone
        from Spotify, so it has to be captured beforehand.
        """
        previous = self.playback_state
        playback = self.check_playback_status()
        if not self.owns_playback(playback):
            return False

        # A paused player would otherwise rewrite an identical row every tick,
        # indefinitely, for no benefit.
        if self.playback_state == previous:
            return False

        try:
            utils.persist_playback_state(self.rfid, self.playback_state)
            logging.debug(
                "Refreshed playback state for RFID %s: %s",
                self.rfid, self.playback_state)
            return True
        except (sqlite3.DatabaseError, ValueError) as e:
            self.handle_exception("Refreshing playback state failed", e)
            return False

    def check_playback_status(self):
        try:
            response = requests.get(
                f"{self.base_url}/me/player", headers=self._get_headers())
            if response.status_code == 204:
                self.active_device = None
                return None
            response.raise_for_status()
            playback = response.json()
            # Spotify sends explicit nulls for these, so a dict default is not
            # enough - it only applies when the key is absent.
            device_id = (playback.get("device") or {}).get("id")
            self.active_device = device_id
            self.playing = (device_id == self.device_id) and playback.get(
                "is_playing")
            # Free position update: we already have the payload, and every
            # observation of our own playback is one more chance to record
            # where the story actually is.
            if self.owns_playback(playback):
                self.playback_state = self._state_from_playback(playback)
            return playback
        except requests.RequestException as e:
            self.handle_exception("Playback status check failed", e)
            return None

    def play(self):
        url = self._device_url("play")
        position = (self.playback_state.get("offset") or {}).get("position", 0)
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
            logging.info("Playback paused")
        except requests.RequestException as e:
            self.handle_exception("Pause failed", e)

    def toggle_playback(self):
        # Refresh first: a phone may have paused, started or taken the session
        # since we last looked, and every decision below depends on that.
        playback = self.check_playback_status()

        if self.playing:
            self.pause_playback()
        elif self.owns_playback(playback):
            # Our own album, merely paused - resume in place.
            self.resume_playback()
        else:
            # Another device holds the session, or this device was handed
            # foreign content. Reclaim by playing our album explicitly;
            # resume_playback() would play whatever is loaded there.
            logging.info(
                "Reclaiming playback for RFID %s (current playback is not "
                "ours)", self.rfid)
            self.play()

    def ensure_owns_playback(self, action):
        """Refresh playback state, reclaiming our album if it is not ours

        Returns True when this device is already playing our album, so the
        caller can act on it. Returns False after reclaiming, in which case
        the press has been spent restarting our album instead.
        """
        playback = self.check_playback_status()
        if self.owns_playback(playback):
            return True
        logging.info(
            "Reclaiming playback for RFID %s instead of %s", self.rfid, action)
        self.play()
        return False

    def next_track(self):
        if not self.ensure_owns_playback("skipping to next track"):
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
        if not self.ensure_owns_playback("going to the previous track"):
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

    def _persist_state(self, state):
        try:
            utils.persist_playback_state(self.rfid, state)
            logging.info("Playback state saved for RFID %s", self.rfid)
            return True
        except (sqlite3.DatabaseError, ValueError) as e:
            self.handle_exception("Saving playback state failed", e)
            return False

    def save_playback_state(self):
        playback = self.check_playback_status()

        # /me/player reports whatever the account is playing, on any device, so
        # the live payload cannot be trusted here. But refusing to write at all
        # loses the session: transferring playback to a phone and then shutting
        # down leaves nothing to resume from. Fall back to the last position we
        # recorded while we did own playback - self.playback_state is only ever
        # set from our own playback, so it can never carry a foreign position.
        if not self.owns_playback(playback):
            logging.info(
                "Current playback is not ours; saving last known position for "
                "RFID %s", self.rfid)
            return self._persist_state(self.playback_state)

        position_ms = playback.get("progress_ms", 0)
        item = playback.get("item") or {}
        context_uri = (playback.get("context") or {}).get("uri")
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
                    offset_position = self._album_offset(
                        context_parts[2], item, track_uri)
                else:
                    logging.warning(
                        "Unsupported context type: %s", context_uri)
            except Exception as e:
                self.handle_exception("Failed to resolve track position", e)

        self.playback_state = {
            "offset": {"position": offset_position},
            "position_ms": position_ms,
        }

        return self._persist_state(self.playback_state)

    # /albums/{id}/tracks rejects anything above 50 with "Invalid limit", while
    # /playlists/{id}/tracks allows 100. Using the lower bound for both keeps
    # one code path; the extra request on long playlists is irrelevant here,
    # since this only runs on a card switch or shutdown.
    PAGE_LIMIT = 50

    def _find_track_index(self, url, track_uri, uri_of):
        """Absolute index of track_uri within a paginated context listing"""
        headers = self._get_headers()
        params = {"limit": self.PAGE_LIMIT, "offset": 0}

        while True:
            response = requests.get(url, headers=headers, params=params)
            response.raise_for_status()
            page = response.json()

            for index, entry in enumerate(page.get("items", [])):
                if uri_of(entry) == track_uri:
                    # Offset of the page plus position within it. Returning the
                    # bare index put every match beyond the first page up to
                    # 100 tracks too early.
                    return params["offset"] + index

            if not page.get("next"):
                return None
            params["offset"] += params["limit"]

    def _get_track_position_in_playlist(self, playlist_id, track_uri):
        index = self._find_track_index(
            f"{self.base_url}/playlists/{playlist_id}/tracks",
            track_uri,
            lambda entry: (entry.get("track") or {}).get("uri"))
        if index is None:
            logging.warning("Track URI not found in playlist")
            return 0  # fallback
        return index

    def _get_track_position_in_album(self, album_id, track_uri):
        index = self._find_track_index(
            f"{self.base_url}/albums/{album_id}/tracks",
            track_uri,
            lambda entry: entry.get("uri"))
        if index is None:
            logging.warning("Track URI not found in album")
            return 0  # fallback
        return index

    def _album_offset(self, album_id, item, track_uri):
        """Index of the current track within an album context

        track_number restarts at 1 on each disc, so on a multi-disc album it is
        not the offset into the context - a track on disc 2 would resume near
        the start of disc 1. Single-disc albums, which is all of ours today,
        keep the free path and make no extra request.
        """
        disc_number = item.get("disc_number") or 1
        if disc_number <= 1:
            return max(0, item.get("track_number", 1) - 1)

        logging.debug(
            "Multi-disc album (disc %d); resolving offset from the track list",
            disc_number)
        return self._get_track_position_in_album(album_id, track_uri)

    def handle_exception(self, message, e):
        logging.error("%s: %s", message, e, exc_info=True)
