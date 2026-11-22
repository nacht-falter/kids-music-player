import json
import logging
import re
import subprocess


def mpc(*args, timeout=5):
    """Run one mpc command, returning its stdout, or None if it failed

    An argument list, never a shell string: card locations are folder names
    like "Barenaked Ladies - Snacktime!", which a shell split into words - so
    the album never got added - and which could carry anything else a shell
    would act on.
    """
    try:
        result = subprocess.run(["mpc", *args], capture_output=True,
                                text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        logging.error("mpc %s failed: %s", " ".join(args), e)
        return None
    if result.returncode != 0:
        logging.error("mpc %s failed: %s", " ".join(args),
                      result.stderr.strip() or "exit %d" % result.returncode)
        return None
    return result.stdout


def status_line():
    """The "[playing] #3/12   0:07/3:21 (3%)" line, or None when stopped

    `mpc status` prints three lines while a track is loaded and only the
    "volume: ..." line once playback has stopped, e.g. at the end of an album.
    """
    lines = (mpc("status") or "").splitlines()
    return lines[1] if len(lines) > 1 else None


class AudioPlayer:
    # See SpotifyPlayer.RESTART_THRESHOLD_MS.
    RESTART_THRESHOLD_SECONDS = 3

    def __init__(self, rfid, playback_state, location):
        self.rfid = rfid
        self.playback_state = (
            json.loads(playback_state)
            if playback_state
            else {"track": 1, "position": "0%"}
        )
        self.location = location
        self.playing = False
        logging.info("AudioPlayer initialized for RFID %s", self.rfid)

    def check_playback_status(self):
        line = status_line()
        # No state line means stopped, which has to count as not playing:
        # leaving `playing` as it was kept a finished album "playing" forever,
        # so the idle watchdog never powered the box off.
        match = re.search(r"\[(\w+)\]", line) if line else None
        self.playing = bool(match) and match.group(1) == "playing"

    def play(self):
        logging.info("Playing: %s", self.location)
        if (mpc("-q", "clear") is None
                or mpc("-q", "add", self.location) is None):
            # Usually a location that is not in mpd's library: a typo at
            # registration, or files copied without an `mpc update`.
            self.playing = False
            # Lazy import to avoid circular dependency: utils imports AudioPlayer
            import utils
            utils.play_sound("playback_error")
            return

        track = str(self.playback_state.get("track") or "").strip()
        # A track number saved while mpd was stopped is empty; start at 1.
        mpc("-q", "play", track if track.isdigit() else "1")

        position = str(self.playback_state.get("position") or "").strip()
        if position and position != "0%":
            mpc("-q", "seek", position)
        self.playing = True

    def toggle_playback(self):
        mpc("-q", "toggle")
        self.playing = not self.playing
        logging.info("Toggled playback: %s",
                     "Playing" if self.playing else "Paused")

    def pause_playback(self):
        mpc("-q", "pause")
        self.playing = False
        logging.info("Playback paused")

    def next_track(self):
        mpc("-q", "next")
        self.playing = True
        logging.info("Next track")

    def previous_track(self):
        # Same rule as SpotifyPlayer: past the first few seconds, restart the
        # current track instead of skipping back.
        if self._elapsed_seconds() > self.RESTART_THRESHOLD_SECONDS:
            mpc("-q", "seek", "0")
            self.playing = True
            logging.info("Restarted the current track")
            return

        mpc("-q", "prev")
        self.playing = True
        logging.info("Previous track")

    def _elapsed_seconds(self):
        """Seconds into the current track, or 0 if it cannot be read"""
        line = status_line()
        match = re.search(r"(\d+):(\d\d)/", line) if line else None
        return int(match.group(1)) * 60 + int(match.group(2)) if match else 0

    def restart_playback(self):
        self.playback_state = {"track": 1, "position": "0%"}
        self.play()
        logging.info("Restarting playback")

    def refresh_playback_state(self):
        """Periodically record position; mpc queries are local and cheap

        Re-reads mpd first. The idle watchdog takes `playing` from here, and
        nothing else updates it when an album simply runs out.
        """
        self.check_playback_status()
        if self.playing:
            self.save_playback_state()

    def save_playback_state(self):
        # Lazy import to avoid circular dependency: utils imports AudioPlayer
        import utils

        try:
            line = status_line()
            if line is None:
                # Stopped. The player only ever pauses, so mpd stops on its
                # own only when the album has run out: the next scan should
                # start it over, not replay the last seconds of its end.
                self.playback_state = {"track": 1, "position": "0%"}
            else:
                track = (mpc("current", "-f", "%position%") or "").strip()
                match = re.search(r"\((\d+)%\)", line)
                self.playback_state = {
                    "track": track or self.playback_state.get("track", 1),
                    "position": match.group(1) + "%" if match else "0%",
                }
            utils.persist_playback_state(self.rfid, self.playback_state)
            logging.info("Playback state saved for RFID %s", self.rfid)
        except Exception as e:
            logging.error("Error saving playback state: %s", e, exc_info=True)
