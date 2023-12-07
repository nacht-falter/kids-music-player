from threading import Timer
import sqlite3
import spotify
import json


def get_command(db, rfid):
    """Get command to execute from database"""
    cursor = db.cursor()
    cursor.execute("SELECT * FROM controls where rfid = ?", (rfid,))
    command = cursor.fetchone()

    if command:
        return command[1]
    else:
        return None


def get_music_data(db, rfid):
    """Get music data from database"""
    cursor = db.cursor()
    cursor.execute(f"SELECT * FROM music WHERE rfid = {rfid}")
    result = cursor.fetchone()
    if result:
        columns = [desc[0] for desc in cursor.description]
        data = dict(zip(columns, result))
        return data
    else:
        return None


class SpotifyPlayer:
    """Spotify player class

    Attributes:
    rfid (str): RFID tag
    playback_state (dict): Playback state
    location (str): Spotify URI

    Methods:
    play(): Play music
    toggle_playback(): Toggle playback
    next_track(): Play next track
    previous_track(): Play previous track
    pause(): Pause playback
    restart_playback(): Restart playback
    save_playback_state(): Save playback state to database
    """

    def __init__(self, rfid, playback_state, location):
        self.rfid = rfid
        self.location = location
        self.playback_state = (
            json.loads(playback_state)
            if playback_state
            else {"offset": 0, "position_ms": 0}
        )
        self.playing = False

    def play(self):
        spotify.play(
            spotify.base_url,
            spotify.headers,
            spotify.device_id,
            self.location,
            self.playback_state["offset"],
            self.playback_state["position_ms"],
        )
        self.playing = True

    def toggle_playback(self):
        spotify.toggle_playback(
            spotify.base_url, spotify.headers, spotify.device_id
        )
        self.playing = not self.playing

    def next_track(self):
        spotify.next_track(
            spotify.base_url, spotify.headers, spotify.device_id
        )

    def previous_track(self):
        spotify.previous_track(
            spotify.base_url, spotify.headers, spotify.device_id
        )

    def pause(self):
        spotify.pause_playback(
            spotify.base_url, spotify.headers, spotify.device_id
        )
        self.playing = False

    def restart_playback(self):
        spotify.play(
            spotify.base_url,
            spotify.headers,
            spotify.device_id,
            self.location,
            0,
            0,
        )
        self.playing = True

    def save_playback_state(self):
        playback_state = spotify.check_playback_status(
            spotify.base_url, spotify.headers
        )
        position_ms = playback_state.get("progress_ms")
        track_number = playback_state.get("item").get("track_number")
        self.playback_state = {
            "offset": track_number - 1,
            "position_ms": position_ms,
        }
        with sqlite3.connect("toem.db") as db:
            db.cursor().execute(
                "UPDATE music SET playback_state = ? WHERE rfid = ?",
                (json.dumps(self.playback_state), self.rfid),
            )


def shutdown(player):
    """Shutdown computer"""
    if not getattr(player, "playing", False):
        print("\nShutting down...")


def main():
    player = None
    restart_counter = 0
    shutdown_counter = 0

    while True:
        # Wait for RFID input
        timeout = 3600
        timer = Timer(timeout, shutdown, [player])
        timer.start()
        rfid = input("Enter RFID: ")
        timer.cancel()

        if not rfid:
            break

        # Check if RFID is already playing
        if player and rfid == player.rfid:
            if restart_counter > 0 and player.playing:
                player.restart_playback()
                restart_counter = 0
            elif restart_counter == 0 and not player.playing:
                player.toggle_playback()
            else:
                print("Already playing")
                restart_counter += 1

        else:
            # Get command and music data from database
            with sqlite3.connect("toem.db") as db:
                command = get_command(db, rfid)
                music_data = get_music_data(db, rfid)

            # Execute command or play music
            if command:
                if command == "shutdown":
                    if shutdown_counter > 0:
                        if player:
                            player.pause()
                            player.save_playback_state()
                        break
                    else:
                        print("confirm shutdown")
                        shutdown_counter += 1
                else:
                    if player:
                        getattr(player, command)()
                    else:
                        print("No player found")

            elif music_data:
                if player:
                    player.pause()
                    player.save_playback_state()
                player = SpotifyPlayer(
                    music_data["rfid"],
                    music_data["playback_state"],
                    music_data["location"],
                )
                player.play()

            restart_counter = 0
            shutdown_counter = 0

    shutdown(player)


if __name__ == "__main__":
    main()
