import json
import logging
import os
import re


class AudioPlayer:
    def __init__(self, rfid, playback_state, location, db):
        self.db = db
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
        os.system("mpc -q prev")
        self.playing = True
        logging.info("Previous track")

    def restart_playback(self):
        self.playback_state = {"track": 1, "position": "0%"}
        self.play()
        logging.info("Restarting playback")

    def save_playback_state(self):
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
            self.db.cursor().execute(
                "UPDATE music SET playback_state = ? WHERE rfid = ?",
                (json.dumps(self.playback_state), self.rfid),
            )
            logging.info("Playback state saved for RFID %s", self.rfid)
        except Exception as e:
            logging.error("Error saving playback state: %s", e, exc_info=True)
