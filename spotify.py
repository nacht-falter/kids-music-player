import datetime
import json
import logging
import os
import sqlite3
import time

import requests

import utils


class SpotifyAuthError(requests.RequestException):
    """Raised when no usable access token is available

    `permanent` separates two very different situations that both surface as
    "no token": Spotify rejecting the credentials (re-authorization needed, and
    retrying is pointless) versus simply not having managed to fetch one yet
    (no network at boot, say - where retrying is the whole point). Treating the
    second as permanent made a card scanned seconds after power-on fail while
    the wifi was still associating.

    Subclasses RequestException so the existing `except requests.RequestException`
    handlers treat it like any other API failure. Raising something they do not
    catch would propagate to main.run(), which returns 1, and systemd's
    StartLimitBurst would then park the unit in a failed state after five
    restarts - turning an expired token into a device that stays dead.
    """

    def __init__(self, *args, permanent=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.permanent = permanent


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


# Spotify expires refresh tokens roughly six months after authorization.
REFRESH_TOKEN_LIFETIME_DAYS = 183
EXPIRY_WARNING_DAYS = 30


def check_refresh_token_age():
    """Warn before the refresh token expires, rather than discovering it after

    Spotify never reports when a token was issued, so this depends on
    SPOTIFY_AUTH_DATE, which reauth.py records. Installs predating that have no
    value to check; unknown is treated as unknown, not as expired.

    Deliberately log-only, with no sound: the warning window is a month long,
    and a device that chirped at every startup for a month would just teach
    everyone to ignore it. Actual failure is already audible via the existing
    playback_error path.

    Returns days remaining, or None when it cannot be determined.
    """
    raw = os.environ.get("SPOTIFY_AUTH_DATE", "").strip()
    if not raw:
        logging.debug(
            "SPOTIFY_AUTH_DATE is not set, so refresh token age is unknown. "
            "It gets recorded by reauth.py on the next re-authorization.")
        return None

    try:
        issued = datetime.date.fromisoformat(raw)
    except ValueError:
        logging.warning("SPOTIFY_AUTH_DATE is not an ISO date: %r", raw)
        return None

    days_left = REFRESH_TOKEN_LIFETIME_DAYS - (datetime.date.today() - issued).days

    if days_left <= 0:
        logging.error(
            "Spotify refresh token is %d day(s) past its expected lifetime "
            "(authorized %s). Playback will fail with invalid_grant. "
            "Run: python reauth.py --host <device>", -days_left, issued)
    elif days_left <= EXPIRY_WARNING_DAYS:
        logging.warning(
            "Spotify refresh token expires in about %d day(s) (authorized "
            "%s). Run: python reauth.py --host <device>", days_left, issued)
    else:
        logging.debug(
            "Spotify refresh token has about %d day(s) left", days_left)

    return days_left



def device_is_playing():
    """Whether our configured Spotify device is currently playing anything

    Deliberately independent of any SpotifyPlayer. The idle watchdog needs to
    know the speaker is in use even when no card has been scanned since boot -
    audio pushed to it from a phone, say - and in that state there is no player
    object to ask.

    Returns False on any doubt (no token, no device configured, network error):
    the caller uses this to *prevent* an idle shutdown, so failing closed keeps
    the battery-saving behaviour rather than pinning the device awake.
    """
    device_id = os.environ.get("SPOTIFY_DEVICE_ID")
    if not device_id:
        return False

    try:
        token = get_auth_manager().get_token()
        if not token:
            return False

        response = requests.get(
            "https://api.spotify.com/v1/me/player",
            headers={"Authorization": "Bearer " + token})
        if response.status_code == 204:
            return False
        response.raise_for_status()

        playback = response.json()
        return ((playback.get("device") or {}).get("id") == device_id
                and bool(playback.get("is_playing")))
    except (requests.RequestException, ValueError) as e:
        logging.debug("Could not check whether the device is playing: %s", e)
        return False


_auth_manager = None


def get_auth_manager():
    global _auth_manager
    if _auth_manager is None:
        _auth_manager = SpotifyAuthManager()
    return _auth_manager


class SpotifyPlayer:
    # How far into a track "previous" stops meaning "the previous track" and
    # starts meaning "this one again". 3s is what the bash version used.
    # Whether re-scanning this card should advance rather than restart.
    # Explicit rather than probing for a next_episode method, so a test double
    # cannot accidentally look like a series.
    is_series = False

    RESTART_THRESHOLD_MS = 3000

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
        # Whether this process has driven playback on this device, and so
        # knows which context is loaded there. Until it has, the account's
        # idea of "our album, paused" may describe a device that has been
        # handed the session and not yet attached to anything.
        self.playback_started = False
        # (position_ms, time.monotonic()) of the last reading we believed.
        # Purely diagnostic today: it lets an impossible position be compared
        # against how much time really elapsed, which is the one measurement
        # librespot's clock cannot distort.
        self.last_good_position = None
        logging.info("SpotifyPlayer initialized for RFID %s", rfid)

    def _get_headers(self):
        token = self.auth_manager.get_token()
        if not token:
            # Previously this sent "Bearer None" and let Spotify reject it,
            # which buried the real cause under a 401 from whichever call
            # happened to be next.
            rejected = bool(self.auth_manager.rejected_at)
            raise SpotifyAuthError(
                "No Spotify access token available; "
                + ("re-authorization needed" if rejected
                   else "could not reach Spotify to fetch one"),
                permanent=rejected)
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
        except SpotifyAuthError:
            # Not retryable: re-raise so create_player stops immediately
            # rather than spending ten seconds proving it again.
            raise
        except requests.RequestException as e:
            self.handle_exception("Transfer playback failed", e)
            return False

    def is_ready(self):
        playback = self.check_playback_status()
        return playback is not None and self.active_device == self.device_id

    def _context_uri(self):
        """The Spotify context this card plays

        A hook rather than self.location directly, so a series card can play
        the album of its current episode while location stays the playlist.
        """
        return self.location

    def _owned_contexts(self):
        """Every context that counts as this card's own playback

        A set so a series card can own each of its episodes' albums. Keep it
        exact: this is what stops foreign content being adopted.
        """
        return {self.location}

    def owns_playback(self, playback):
        """Whether the current playback is this device playing our own album

        False when another device holds the session, or when this device was
        handed something else (e.g. a podcast transferred from a phone).
        """
        if not playback:
            return False
        device_id = (playback.get("device") or {}).get("id")
        context_uri = (playback.get("context") or {}).get("uri")
        return device_id == self.device_id and context_uri in self._owned_contexts()

    # A track may legitimately report slightly past its own end between the
    # last tick and the track change, so allow a little slack before calling a
    # position impossible.
    POSITION_GRACE_MS = 5000

    # How far a reported position may disagree with the monotonic clock before
    # it is treated as a clock artefact rather than real playback. Observed
    # jitter (the request round-trip, the server's own extrapolation) sits
    # around half a second; the smallest real fault seen in the wild was 37s.
    POSITION_DRIFT_TOLERANCE_MS = 5000

    def _note_good_position(self, playback, position_ms):
        """Anchor a position we accepted against the monotonic clock

        Only a playing position anchors. A paused player's position stands
        still while monotonic time keeps moving, so anchoring one would make
        the pause itself look like drift and reject everything after it.
        """
        if not playback.get("is_playing"):
            self.last_good_position = None
            return
        track_id = (playback.get("item") or {}).get("id")
        self.last_good_position = (position_ms, time.monotonic(), track_id)

    def _note_intended_position(self, position_ms):
        """Anchor on the position we asked for, not one we were told

        The corruption survives a fresh play() - observed in the wild, with
        impossible readings continuing through three consecutive restarts - so
        a session can begin already poisoned. Anchoring on the first reading
        back would adopt that poison as truth and accept everything after it.
        What we requested is known to be right, and monotonic time from the
        request onward is all that is needed to police it.

        The track is left unknown: we asked for an offset, not an id, so the
        first reading defines it.
        """
        self.last_good_position = (position_ms, time.monotonic(), None)

    def _forget_good_position(self):
        """Drop the anchor because playback is about to move on purpose

        A skip or a resume severs the relationship between elapsed time and
        where playback should be, and unlike play() we cannot say where it
        landed. Without this the first reading afterwards looks exactly like
        a clock jump and would be thrown away. Moves whose destination we do
        know use _note_intended_position() instead.
        """
        self.last_good_position = None

    def _position_diagnostics(self, playback, position_ms):
        """Everything needed to diagnose a bad position after the fact

        The trigger is a systemd-timesyncd step: on this RTC-less Pi every
        boot starts on a restored fake-hwclock stamp, time-sync.target is
        reached before NTP has actually answered, and the real correction
        lands 42-89s later - by which time a child is usually listening.
        Confirmed against five separate clusters, each beginning within
        seconds of an "Initial synchronization to time server" line, each
        offset by exactly the size of the step.

        Kept because the offset, its sign and the uptime are what identify a
        recurrence as this and not something new.
        """
        item = playback.get("item") or {}
        fields = [
            f"reported_ms={position_ms}",
            f"api_timestamp={playback.get('timestamp')}",
            f"system_clock={datetime.datetime.now().isoformat(timespec='seconds')}",
            f"monotonic={time.monotonic():.1f}",
            f"is_playing={playback.get('is_playing')}",
            f"track={item.get('id')}",
            f"duration_ms={item.get('duration_ms')}",
            f"context={(playback.get('context') or {}).get('uri')}",
        ]

        try:
            with open("/proc/uptime") as fh:
                fields.append(f"uptime_s={float(fh.read().split()[0]):.0f}")
        except OSError:
            pass

        if self.last_good_position:
            good_ms, good_mono, _ = self.last_good_position
            elapsed_ms = (time.monotonic() - good_mono) * 1000
            # If the clock were honest these two would match. The gap is the
            # size of the distortion, and its sign says which way.
            fields.append(f"last_good_ms={good_ms}")
            fields.append(f"monotonic_elapsed_ms={elapsed_ms:.0f}")
            fields.append(f"expected_ms={good_ms + elapsed_ms:.0f}")
            fields.append(f"discrepancy_ms={position_ms - (good_ms + elapsed_ms):.0f}")
        else:
            fields.append("last_good_ms=none")

        return " ".join(fields)

    @staticmethod
    def _position_is_impossible(position_ms, item):
        """Whether a reported position cannot correspond to real playback

        spotifyd derives progress from the wall clock. This Pi has no RTC, so
        at boot it starts on a restored fake-hwclock stamp and jumps when NTP
        finally syncs; a jump while spotifyd is running leaves its progress
        reports minutes negative, or past the end of the track.

        Persisting that overwrites a good saved position with nonsense, and the
        next scan then resumes somewhere the child does not expect - which is
        the whole failure this position tracking exists to prevent.
        """
        if position_ms is None or position_ms < 0:
            return True

        duration_ms = (item or {}).get("duration_ms")
        return bool(duration_ms
                    and position_ms > duration_ms + SpotifyPlayer.POSITION_GRACE_MS)

    def _position_contradicts_elapsed(self, playback, position_ms):
        """Whether a position disagrees with how much time has actually passed

        The bounds check above only sees a position outside the track. A clock
        step smaller than the track duration lands inside those bounds and
        reads as entirely ordinary - the child simply resumes somewhere they
        never were. time.monotonic() cannot be stepped, so it is the one
        reference that still holds when the wall clock moves.

        Deliberately silent unless nothing else could explain the gap. The
        anchor is dropped on every pause, skip and resume, and a track change
        is checked here, so a surviving anchor means uninterrupted playback of
        one track - and then reported position and elapsed time must agree.
        """
        if not playback.get("is_playing") or not self.last_good_position:
            return False

        good_ms, good_mono, good_track = self.last_good_position
        if (good_track is not None
                and good_track != (playback.get("item") or {}).get("id")):
            return False

        elapsed_ms = (time.monotonic() - good_mono) * 1000
        drift_ms = position_ms - (good_ms + elapsed_ms)
        return abs(drift_ms) > self.POSITION_DRIFT_TOLERANCE_MS

    def _position_rejection_reason(self, playback, position_ms, item):
        """Why a reported position cannot be trusted, or None if it can"""
        if self._position_is_impossible(position_ms, item):
            return "impossible"
        if self._position_contradicts_elapsed(playback, position_ms):
            return "drifted"
        return None

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

        position_ms = playback.get("progress_ms", 0)
        reason = self._position_rejection_reason(playback, position_ms, item)
        if reason:
            # Keep what we had rather than recording a position that cannot be
            # real; a clock jump must not cost the child their place.
            logging.warning(
                "Ignoring %s playback position for RFID %s: %s",
                reason, self.rfid,
                self._position_diagnostics(playback, position_ms))
            return dict(self.playback_state)

        self._note_good_position(playback, position_ms)

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
            "position_ms": position_ms,
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
                # Nothing is playing anywhere. Leaving the cached flag alone
                # kept it True after playback stopped, so callers reasoning
                # about whether we are still playing read stale state.
                self.playing = False
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
            "context_uri": self._context_uri(),
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
            self._note_intended_position(position_ms)
            logging.info(
                "Started playback from beginning at position %d (%d ms)", position, position_ms)
        except requests.RequestException as e:
            self.handle_exception("Playback failed", e, audible=True)

    def resume_playback(self):
        """Continue what this device is already playing

        Only correct once we know the context is loaded there, which is what
        playback_started means - see toggle_playback(). A bare resume carries
        no context of its own: it says "continue whatever is loaded", and a
        device that has just been handed the session has nothing to continue.
        """
        url = self._device_url("play")
        try:
            response = requests.put(url, headers=self._get_headers(), json={})
            response.raise_for_status()
            self.playing = True
            self.playback_started = True
            self.active_device = self.device_id
            self._forget_good_position()
            logging.info("Resumed playback on device %s", self.device_id)
        except requests.RequestException as e:
            self.handle_exception("Resuming playback failed", e, audible=True)

    def pause_playback(self):
        if not self.playing:
            return
        url = self._device_url("pause")
        try:
            response = requests.put(url, headers=self._get_headers())
            response.raise_for_status()
            self.playing = False
            # We have now driven this player's playback, so a later button
            # press must toggle rather than start the album afresh at the
            # stored position.
            self.playback_started = True
            self._forget_good_position()
            logging.info("Playback paused")
        except requests.RequestException as e:
            self.handle_exception("Pause failed", e, audible=True)

    def toggle_playback(self):
        # Refresh first: a phone may have paused, started or taken the session
        # since we last looked, and every decision below depends on that.
        playback = self.check_playback_status()

        if self.playing:
            self.pause_playback()
        elif self.playback_started and self.owns_playback(playback):
            # Our own album, merely paused, and we are the ones who put it
            # there - so the device has the context and can continue it.
            self.resume_playback()
        elif self.owns_playback(playback):
            # The account says our album is loaded here, but nothing in this
            # process put it there: the player was rebuilt under a running
            # spotifyd, or the session was only just transferred. Name the
            # context rather than asking to continue one that may not be
            # attached yet. check_playback_status() has just refreshed the
            # position from the live payload, so this resumes where the story
            # actually is rather than seeking to the stored position.
            self.play()
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
            self._forget_good_position()
            logging.info("Skipped to next track")
        except requests.RequestException as e:
            self.handle_exception("Next track failed", e, audible=True)

    def restart_track(self):
        """Seek to the start of the current track"""
        url = (f"{self.base_url}/me/player/seek"
               f"?position_ms=0&device_id={self.device_id}")
        try:
            response = requests.put(url, headers=self._get_headers())
            response.raise_for_status()
            self.playing = True
            self._note_intended_position(0)
            logging.info("Restarted the current track")
        except requests.RequestException as e:
            self.handle_exception("Restarting the track failed", e, audible=True)

    def previous_track(self):
        if not self.ensure_owns_playback("going to the previous track"):
            return

        # Past the first few seconds, "previous" restarts the current track
        # rather than skipping back - what a CD player does, what the bash
        # version did, and what stops a child losing their place by one press
        # too many. ensure_owns_playback() just refreshed the position.
        position_ms = self.playback_state.get("position_ms", 0)
        if position_ms > self.RESTART_THRESHOLD_MS:
            logging.info("%.1fs into the track, restarting it instead",
                         position_ms / 1000)
            self.restart_track()
            return

        url = self._device_url("previous")
        try:
            response = requests.post(url, headers=self._get_headers())
            response.raise_for_status()
            self.playing = True
            self._forget_good_position()
            logging.info("Returned to previous track")
        except requests.RequestException as e:
            self.handle_exception("Previous track failed", e, audible=True)

    def restart_playback(self):
        url = self._device_url("play")
        data = {"context_uri": self._context_uri(),
                "offset": {"position": 0}, "position_ms": 0}
        try:
            response = requests.put(
                url, headers=self._get_headers(), json=data)
            response.raise_for_status()
            self.playing = True
            self._note_intended_position(0)
            logging.info("Playback restarted from beginning")
        except requests.RequestException as e:
            self.handle_exception("Restart failed", e, audible=True)

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

        reason = self._position_rejection_reason(playback, position_ms, item)
        if reason:
            # As in _state_from_playback: a clock jump makes the live reading
            # worthless, and the last position we recorded is better than one
            # that cannot be real. Resolving the offset below would also be
            # wasted API calls.
            logging.warning(
                "%s playback position for RFID %s; keeping the last known "
                "position: %s", reason.capitalize(), self.rfid,
                self._position_diagnostics(playback, position_ms))
            return self._persist_state(self.playback_state)

        self._note_good_position(playback, position_ms)

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

    def handle_exception(self, message, e, audible=False):
        """Log a failure, and optionally tell whoever is standing there

        audible=True for actions a child initiated - a scan or a button press -
        where silence cannot be told apart from a broken device. Background
        work (status polling, saving state) stays quiet: a beep there would be
        noise, and inside create_player's retry loop it would fire ten times.
        """
        # A RequestException already says everything useful in its message -
        # status code and URL - and its traceback is the same raise_for_status
        # frame every time. Inside create_player's retry loop that meant ten
        # identical stack traces per failed scan, burying the one line that
        # actually mattered. Anything else is unexpected, and there the
        # traceback is the whole point: the AttributeError that killed the
        # watchdog thread on 2026-08-17 was only diagnosable from its stack.
        unexpected = not isinstance(e, requests.RequestException)
        logging.error("%s: %s", message, e, exc_info=unexpected)

        if audible:
            utils.play_sound("playback_error")


class SpotifySeriesPlayer(SpotifyPlayer):
    """A card whose location is a playlist of episodes, one album per episode

    Audio dramas publish each episode as its own Spotify album. A playlist of
    those albums, curated by hand, is the series definition: the order is
    explicit, so nothing has to be inferred from album titles. Episode
    boundaries come from album identity, which every playlist track carries.

    Playback uses the *album* of the current episode as its context, never the
    playlist. That is what makes an episode stop at its own end instead of
    rolling into the next one, with no timer to schedule and no boundary to
    race - and it lets resume inside an episode reuse the inherited
    offset/position logic untouched.
    """

    is_series = True

    # How close to the end of an episode's last track still counts as
    # finished. The position is only sampled every STATE_REFRESH_INTERVAL
    # seconds, so the last sample before the end can be well short of it.
    FINISHED_TOLERANCE_MS = 45000

    def __init__(self, rfid, playback_state, location):
        super().__init__(rfid, playback_state, location)
        self.playlist_id = location.split(":")[-1]
        self.episodes = self._load_episodes()

        index = self.playback_state.get("episode", 0)
        if not isinstance(index, int) or not 0 <= index < len(self.episodes):
            # The playlist may have been edited since we last played, or this
            # is the first ever scan. Either way, start at the beginning
            # rather than addressing an episode that is not there.
            if index:
                logging.warning(
                    "Stored episode %r is outside the %d episodes of this "
                    "series; starting from the first", index, len(self.episodes))
            index = 0
        self.episode_index = index

        if self.episodes and self._current_episode_finished():
            self._advance_episode(+1, reason="the previous episode finished")

        logging.info("Series %s: %d episodes, starting at episode %d",
                     rfid, len(self.episodes), self.episode_index + 1)

    # --- the series definition -------------------------------------------

    def _load_episodes(self):
        """Ordered episodes of the playlist, from cache when it is still valid

        Paging an 80-episode playlist is a dozen-odd requests, far too slow to
        repeat on every scan while a child waits for sound. The playlist's
        snapshot_id changes whenever it is edited, so one cheap request tells
        us whether the cached map is still good.
        """
        snapshot = self._playlist_snapshot()
        cached = utils.read_series_cache(self.playlist_id)

        if cached and snapshot and cached.get("snapshot_id") == snapshot:
            return cached.get("episodes", [])

        try:
            episodes = self._fetch_episodes()
        except (requests.RequestException, ValueError) as e:
            self.handle_exception("Reading the series playlist failed", e)
            # A stale map beats no map: the playlist changed, but the episodes
            # we knew about are still playable.
            return cached.get("episodes", []) if cached else []

        if episodes and snapshot:
            utils.write_series_cache(self.playlist_id, snapshot, episodes)
        return episodes

    def _playlist_snapshot(self):
        try:
            response = requests.get(
                f"{self.base_url}/playlists/{self.playlist_id}",
                headers=self._get_headers(),
                params={"fields": "snapshot_id"})
            response.raise_for_status()
            return (response.json() or {}).get("snapshot_id")
        except requests.RequestException as e:
            self.handle_exception("Reading the playlist snapshot failed", e)
            return None

    def _fetch_episodes(self):
        """Group the playlist's tracks into episodes by album identity

        A run of consecutive tracks sharing an album is one episode. Runs
        rather than a plain grouping, so a playlist that revisits an album
        later gets two episodes rather than one interleaved mess.
        """
        url = f"{self.base_url}/playlists/{self.playlist_id}/tracks"
        headers = self._get_headers()
        params = {"limit": self.PAGE_LIMIT, "offset": 0}
        episodes = []

        while True:
            response = requests.get(url, headers=headers, params=params)
            response.raise_for_status()
            page = response.json()

            for entry in page.get("items", []):
                track = entry.get("track") or {}
                album = track.get("album") or {}
                uri = album.get("uri")
                if not uri:
                    # Removed tracks, local files and podcast episodes all
                    # arrive without a usable album.
                    continue

                if not episodes or episodes[-1]["uri"] != uri:
                    episodes.append({"uri": uri,
                                     "title": album.get("name"),
                                     "durations": []})
                episodes[-1]["durations"].append(track.get("duration_ms") or 0)

            if not page.get("next"):
                return episodes
            params["offset"] += params["limit"]

    # --- where we are in the series ---------------------------------------

    def current_episode(self):
        if not self.episodes:
            return None
        return self.episodes[self.episode_index]

    def _current_episode_finished(self):
        """Whether the stored position sits at the end of the current episode

        Checked when the player is built - that is, on a card scan - so no
        polling is needed to notice an episode ended.
        """
        episode = self.current_episode()
        if not episode:
            return False

        durations = episode.get("durations") or []
        track = (self.playback_state.get("offset") or {}).get("position", 0)
        if track < len(durations) - 1:
            return False
        if not durations:
            return False

        position_ms = self.playback_state.get("position_ms", 0)
        remaining = durations[-1] - position_ms
        return remaining <= self.FINISHED_TOLERANCE_MS

    def _advance_episode(self, step, reason):
        """Move by whole episodes, wrapping so a card never goes dead"""
        if not self.episodes:
            return
        previous = self.episode_index
        self.episode_index = (self.episode_index + step) % len(self.episodes)
        self.playback_state = {"episode": self.episode_index,
                               "offset": {"position": 0},
                               "position_ms": 0}
        logging.info("Episode %d -> %d (%s): %s", previous + 1,
                     self.episode_index + 1, reason,
                     (self.current_episode() or {}).get("title"))

    def next_episode(self):
        self._advance_episode(+1, reason="next episode")
        self.play()

    def previous_episode(self):
        self._advance_episode(-1, reason="previous episode")
        self.play()

    # --- overrides --------------------------------------------------------

    def _context_uri(self):
        episode = self.current_episode()
        # Falling back to the playlist keeps a series with an unreadable
        # definition playable, rather than silent.
        return episode["uri"] if episode else self.location

    def _owned_contexts(self):
        """Every episode album, so ownership survives an episode change

        Still exact: a podcast pushed from a phone is not in this set, so it
        is never adopted.
        """
        if not self.episodes:
            return {self.location}
        return {episode["uri"] for episode in self.episodes}

    def _state_from_playback(self, playback):
        state = super()._state_from_playback(playback)
        state["episode"] = self.episode_index
        return state

    def _persist_state(self, state):
        # save_playback_state() rebuilds the state dict from scratch and would
        # otherwise drop the episode, silently resetting the child to the
        # first one on the next scan.
        state = dict(state or {})
        state["episode"] = self.episode_index
        self.playback_state = state
        return super()._persist_state(state)
