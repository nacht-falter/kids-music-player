import os
import sqlite3
import json


class AudioPlayer:
    def __init__(self, rfid, playback_state, location):
        self.database_url = os.environ.get("DATABASE_URL")
        self.rfid = rfid
        self.playback_state = (
            json.loads(playback_state)
            if playback_state
            else {"track": 1, "position": "0%"}
        )
        self.location = location
        self.playing = False

    def play(self):
        os.system(f"mpc -q clear; mpc -q add {self.location}; mpc -q play")
        os.system("mpc -q play " + str(self.playback_state["track"]))
        os.system("mpc -q seek " + str(self.playback_state["position"]))
        self.playing = True

    def toggle_playback(self):
        os.system("mpc -q toggle")
        self.playing = not self.playing

    def pause_playback(self):
        os.system("mpc -q pause")
        self.playing = False

    def next_track(self):
        os.system("mpc -q next")
        self.playing = True

    def previous_track(self):
        os.system("mpc -q prev")
        self.playing = True

    def restart_playback(self):
        self.playback_state = {"track": 1, "position": "0%"}
        self.play()

    def save_playback_state(self):
        track_number = os.popen("mpc current -f %position%").read().strip()
        mpc_status = os.popen("mpc status").readlines()
        if len(mpc_status) > 1:
            position = (
                os.popen(
                    "echo '" + mpc_status[1] + "' | awk -F '[()]' '{print $2}'"
                )
                .read()
                .strip()
            )
        else:
            position = "0%"
        self.playback_state = {
            "track": track_number,
            "position": position,
        }
        with sqlite3.connect(self.database_url) as db:
            db.cursor().execute(
                "UPDATE music SET playback_state = ? WHERE rfid = ?",
                (json.dumps(self.playback_state), self.rfid),
            )
