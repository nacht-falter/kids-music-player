import json
import logging
import os
import re


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
        mpc_status = os.popen("mpc status").readlines()
        pattern = r"\[(\w+)\]"
        if len(mpc_status) > 1:
            match = re.search(pattern, mpc_status[1])
            if match:
                self.playing = match.group(1) == "playing"

    def play(self):
        logging.info("Playing: %s", self.location)
        os.system(f"mpc -q clear; mpc -q add {self.location}; mpc -q play")
        os.system("mpc -q play " + str(self.playback_state["track"]))
        os.system("mpc -q seek " + str(self.playback_state["position"]))
        self.playing = True

    def toggle_playback(self):
        os.system("mpc -q toggle")
        self.playing = not self.playing
        logging.info("Toggled playback: %s",
                     "Playing" if self.playing else "Paused")

    def pause_playback(self):
        os.system("mpc -q pause")
        self.playing = False
        logging.info("Playback paused")

    def next_track(self):
        os.system("mpc -q next")
        self.playing = True
        logging.info("Next track")

    def previous_track(self):
        # Same rule as SpotifyPlayer: past the first few seconds, restart the
        # current track instead of skipping back.
        if self._elapsed_seconds() > self.RESTART_THRESHOLD_SECONDS:
            os.system("mpc -q seek 0")
            self.playing = True
            logging.info("Restarted the current track")
            return

        os.system("mpc -q prev")
        self.playing = True
        logging.info("Previous track")

    def _elapsed_seconds(self):
        """Seconds into the current track, or 0 if it cannot be read"""
        try:
            status = os.popen("mpc status").readlines()
            if len(status) < 2:
                return 0
            # e.g. "[playing] #3/12   0:07/3:21 (3%)"
            match = re.search(r"(\d+):(\d\d)/", status[1])
            return int(match.group(1)) * 60 + int(match.group(2)) if match else 0
        except Exception as e:
            logging.debug("Could not read elapsed time: %s", e)
            return 0

    def restart_playback(self):
        self.playback_state = {"track": 1, "position": "0%"}
        self.play()
        logging.info("Restarting playback")

    def refresh_playback_state(self):
        """Periodically record position; mpc queries are local and cheap"""
        if self.playing:
            self.save_playback_state()

    def save_playback_state(self):
        # Lazy import to avoid circular dependency: utils imports AudioPlayer
        import utils

        try:
            track_number = os.popen("mpc current -f %position%").read().strip()
            mpc_status = os.popen("mpc status").readlines()
            position = (
                os.popen(
                    f"echo '{mpc_status[1]}' | awk -F '[()]' '{{print $2}}'")
                .read()
                .strip()
                if len(mpc_status) > 1 else "0%"
            )
            self.playback_state = {"track": track_number, "position": position}
            utils.persist_playback_state(self.rfid, self.playback_state)
            logging.info("Playback state saved for RFID %s", self.rfid)
        except Exception as e:
            logging.error("Error saving playback state: %s", e, exc_info=True)
