import json
import logging
import os
import re
import sqlite3
import subprocess
import time

from local import AudioPlayer

try:
    import led
except ImportError:
    led = None


# Where the resolved episode list for a series playlist is cached. Beside the
# database by default, so it lives with the rest of the device's state.
SERIES_CACHE_PATH = os.environ.get("SERIES_CACHE", "series_cache.json")


def read_series_cache(playlist_id):
    """The cached episode map for a playlist, or None

    Best-effort by design: a missing, unreadable or corrupt cache just means
    the map gets fetched again, which is slower but always correct.
    """
    try:
        with open(SERIES_CACHE_PATH) as cache_file:
            return json.load(cache_file).get(playlist_id)
    except (OSError, ValueError) as e:
        logging.debug("No usable series cache: %s", e)
        return None


def write_series_cache(playlist_id, snapshot_id, episodes):
    """Record the episode map, keyed by the playlist's snapshot

    Written only when the playlist actually changes, so this costs a handful
    of flash writes a year rather than one per scan.
    """
    try:
        try:
            with open(SERIES_CACHE_PATH) as cache_file:
                cache = json.load(cache_file)
        except (OSError, ValueError):
            cache = {}

        cache[playlist_id] = {"snapshot_id": snapshot_id, "episodes": episodes}
        with open(SERIES_CACHE_PATH, "w") as cache_file:
            json.dump(cache, cache_file)
    except OSError as e:
        # Losing the cache costs speed on the next scan, nothing else.
        logging.warning("Could not write the series cache: %s", e)


def persist_playback_state(rfid, playback_state):
    """Write playback state for an RFID to the database

    Uses its own short-lived connection: players are created from both the
    button callback thread and the RFID thread, and a sqlite connection may
    only be used by the thread that created it. Committing here also means the
    write survives shutdown, which exits without touching the shared
    connection.
    """
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise ValueError("DATABASE_URL environment variable is not set")

    db = sqlite3.connect(database_url)
    try:
        with db:  # commits on success, rolls back on error
            db.execute(
                "UPDATE music SET playback_state = ? WHERE rfid = ?",
                (json.dumps(playback_state), rfid),
            )
    finally:
        db.close()


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


# Where spotifyd keeps its credentials. 0.4.x prefers the oauth blob at
# startup, then the zeroconf one; the flat file is what 0.3.x used and 0.4.x
# ignores. Spelled out rather than expanded from "~": spotifyd runs as pi and
# the player runs as root, so this process's home is /root and none of these
# are under it. Overridable for installs laid out differently.
SPOTIFYD_CACHE = os.environ.get("SPOTIFYD_CACHE", "/home/pi/.cache/spotifyd")
SPOTIFYD_OAUTH_BLOB = os.path.join(SPOTIFYD_CACHE, "oauth/credentials.json")
SPOTIFYD_ZEROCONF_BLOB = os.path.join(
    SPOTIFYD_CACHE, "zeroconf/credentials.json")
SPOTIFYD_FLAT_CREDENTIALS = os.path.join(SPOTIFYD_CACHE, "credentials.json")

# Both quote styles: spotifyd 0.3.x writes Authenticated as "x", 0.4.x
# writes 'x'. toem is capped at 0.3.5 (no armv6 binary since 0.4.0) and
# toem2's journal holds both, from before its unit conversion.
AUTHENTICATED_AS = re.compile(r"""Authenticated as ["']([^"']+)["']""")

# spotifyd is a system unit on toem2 and a user unit on toem, and journalctl
# selects those differently. Ask both: a box has one or the other, and
# toem2 has *history* under both, so the answer is the most recent line
# across the two rather than the first selector that returns anything.
SPOTIFYD_JOURNAL_SELECTORS = (
    ["-u", "spotifyd"],
    ["_SYSTEMD_USER_UNIT=spotifyd.service"],
)


def spotifyd_account():
    """The account spotifyd is logged in as right now, or None

    Read from the journal rather than from a credential file. The files say
    what the next start would use; this says who is actually logged in, and
    those diverge exactly when it matters.
    """
    latest = None
    for selector in SPOTIFYD_JOURNAL_SELECTORS:
        try:
            journal = subprocess.run(
                ["journalctl", "--no-pager", "-o", "short-unix", "-n", "500"]
                + selector,
                capture_output=True, text=True, timeout=5).stdout
        except (OSError, subprocess.SubprocessError) as e:
            logging.debug("Could not read spotifyd's journal (%s): %s",
                          " ".join(selector), e)
            continue
        for line in journal.splitlines():
            found = AUTHENTICATED_AS.search(line)
            if not found:
                continue
            try:  # short-unix prefixes each line with its epoch timestamp
                when = float(line.split(" ", 1)[0])
            except ValueError:
                continue
            if latest is None or when > latest[0]:
                latest = (when, found.group(1))
    return latest[1] if latest else None


def blob_account(path):
    """The account named by one of spotifyd's credential files, or None"""
    try:
        with open(path, encoding="utf-8") as blob:
            return json.load(blob).get("username")
    except (OSError, ValueError):
        return None


def explain_missing_device():
    """Log why Spotify has no device for us, when a transfer says it has none

    The 404s that follow a failed transfer say the device is not there but
    never why. Much the likeliest cause is that a Spotify client on the
    network logged spotifyd into a different account - that is what Connect
    discovery is for, and we leave it on so both boxes can be played to from a
    phone. The box then advertises itself to that account instead of ours, and
    stays there across reboots if the credentials were cached.

    So: who spotifyd is logged in as now, and which account each credential
    file would start it as next time. Purely diagnostic, and best-effort -
    anything unreadable is reported as unknown rather than raising.
    """
    running_as = spotifyd_account()
    blobs = ", ".join(
        "%s=%s" % (name, account)
        for name, account in (("oauth", blob_account(SPOTIFYD_OAUTH_BLOB)),
                              ("zeroconf", blob_account(SPOTIFYD_ZEROCONF_BLOB)),
                              ("cached", blob_account(SPOTIFYD_FLAT_CREDENTIALS)))
        if account)

    logging.error("No Spotify device for us: spotifyd is on %s; credentials %s",
                  running_as or "an unknown account", blobs or "unreadable")


def create_player(music_data, retries=10, delay=1):
    """Create audio player instance"""
    rfid = music_data["rfid"]
    source = music_data.get("source")
    playback_state = music_data.get("playback_state")
    location = music_data.get("location")

    logging.info("Creating player for RFID %s", rfid)

    if source in ("spotify", "spotify_series"):
        # Lazy import to avoid circular dependency
        from spotify import (SpotifyAuthError, SpotifyPlayer,
                             SpotifySeriesPlayer)
        player_class = (SpotifySeriesPlayer if source == "spotify_series"
                        else SpotifyPlayer)

        for attempt in range(retries):
            try:
                player = player_class(rfid, playback_state, location)
                transferred = player.transfer_playback(play=False)
            except SpotifyAuthError as e:
                if e.permanent:
                    # Spotify rejected the credentials; retrying cannot produce
                    # a token, so fail now rather than after the full budget.
                    logging.error("Cannot start Spotify playback: %s", e)
                    return None
                # No token *yet* - typically the network is still coming up
                # after a boot. Keep trying; this is what the budget is for.
                logging.warning(
                    "No Spotify token yet (attempt %d/%d): %s",
                    attempt + 1, retries, e)
                time.sleep(delay)
                continue
            # Only ask whether it is ready if the transfer worked. When it
            # did not, the device is not there and is_ready() is a second HTTP
            # round trip to confirm what we already know - which was most of
            # the 17.4s a failing scan took before any sound came out.
            if transferred and player.is_ready():
                logging.info(
                    "Spotify player ready after %d attempt(s)", attempt + 1)
                return player
            # Ten identical 404s say the device is not there but never why.
            # Explain once, on the first failure, then retry as before.
            if attempt == 0 and not transferred:
                explain_missing_device()
            logging.warning("Spotify player not ready (attempt %d/%d), retrying in %d seconds...",
                            attempt + 1, retries, delay)
            time.sleep(delay)

        logging.error(
            "Failed to initialize a ready Spotify player after %d attempts", retries)
        return None

    elif source == "local":
        return AudioPlayer(rfid, playback_state, location)

    else:
        logging.warning("Unknown music source: %s", source)
        return None


def play_sound(event, blocking=False):
    """Play sound file associated with event"""
    sounds = {
        "start": "start",
        "confirm": "confirm",
        "error": "error",
        "next_track": "click",
        "previous_track": "click",
        "toggle_playback": "click",
        # The IR remote has volume keys; toem2 has a hardware pot and no need
        # for these. Files were already in sounds/ from the bash version.
        "volume_up": "volup",
        "volume_down": "voldown",
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
        if blocking:
            subprocess.run(
                ["aplay", file_path],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL)
        else:
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
    """Handle re-scanning the card that is already loaded"""
    # Refresh before branching: the cached flag goes stale whenever something
    # happens outside our control - spotifyd restarting, a phone taking the
    # session - and acting on a stale True quietly does the wrong thing.
    player.check_playback_status()

    if player.playing:
        # Re-scanning a playing card means "go round again". For an album
        # that is the start of the album; for a series it is the next
        # episode, wrapping past the last one. Same gesture, same idea - the
        # series simply has more than one place to go, so this is consistent
        # with existing behaviour rather than a special case.
        #
        # It also replaces the double button press, whose timing could not be
        # measured: the IR reader blocks on the Spotify call, so the "gap"
        # between presses was really the API round-trip (TODO 30). A scan is a
        # discrete event and needs no window at all.
        if getattr(player, "is_series", False):
            logging.info("Already playing a series. Advancing an episode.")
            player.next_episode()
            return
        logging.info("Already playing. Restarting playback.")
        player.restart_playback()
    else:
        logging.info("Playback is paused. Toggling playback.")
        player.toggle_playback()


def shutdown(player, sync_done=None):
    """Shutdown computer"""
    play_sound("shutdown", blocking=True)
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
