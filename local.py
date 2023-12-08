import os
import pygame
import sqlite3
import json


class AudioPlayer:
    def __init__(self, rfid, playback_state, location):
        pygame.init()
        self.database_url = os.environ.get("DATABASE_URL")
        self.rfid = rfid
        self.playback_state = json.loads(playback_state)
        self.location = location
        self.file_list = self.get_audio_files()
        self.current_index = self.playback_state.get("current_index", 0)
        self.playing = False

    def get_audio_files(self):
        files = []
        music_library = os.environ.get("MUSIC_LIBRARY")
        for file in os.listdir(os.path.join(music_library, self.location)):
            if file.endswith(".mp3") or file.endswith(".wav"):
                files.append(os.path.join(music_library, self.location, file))
        return files

    def play(self):
        pygame.mixer.music.load(self.file_list[self.current_index])
        pygame.mixer.music.play(
            start=self.playback_state.get("position", 0) / 1000
        )
        self.playing = True

    def toggle_playback(self):
        if self.playing:
            pygame.mixer.music.pause()
            self.playing = False
        else:
            pygame.mixer.music.unpause()
            self.playing = True

    def pause_playback(self):
        pygame.mixer.music.pause()
        self.playing = False

    def next_track(self):
        self.current_index = (self.current_index + 1) % len(self.file_list)
        self.play()

    def previous_track(self):
        self.current_index = (self.current_index - 1) % len(self.file_list)
        self.play()

    def restart_playback(self):
        self.play()

    def save_playback_state(self):
        current_index = self.current_index
        position = pygame.mixer.music.get_pos()
        self.playback_state = {
            "current_index": current_index,
            "position": position,
        }
        with sqlite3.connect(self.database_url) as db:
            db.cursor().execute(
                "UPDATE music SET playback_state = ? WHERE rfid = ?",
                (json.dumps(self.playback_state), self.rfid),
            )
