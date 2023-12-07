from threading import Timer
import sqlite3
import spotify
import json


def create_tables():
    db = sqlite3.connect("toem.db")
    cursor = db.cursor()
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS music(
            rfid TEXT PRIMARY KEY,
            source TEXT NOT NULL,
            playback_state TEXT,
            location TEXT NOT NULL
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS controls(
            rfid TEXT PRIMARY KEY,
            command TEXT NOT NULL
        )
        """
    )
    db.commit()
    db.close()


def create_new_db_entry(db, rfid, source, location):
    cursor = db.cursor()
    cursor.execute(
        "INSERT INTO music (rfid, source, location) VALUES (?, ?, ?)",
        (rfid, source, location),
    )
    db.commit()


def get_playback_state():
    spotify_playback_status = spotify.check_playback_status(
        spotify.base_url, spotify.headers
    ).get("is_playing")
    print("Spotify playing: ", spotify_playback_status)


def get_command(db, rfid):
    cursor = db.cursor()
    cursor.execute("SELECT * FROM controls where rfid = ?", (rfid,))
    command = cursor.fetchone()

    if command:
        return command[1]
    else:
        return None


def get_music_data(db, rfid):
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
    if not getattr(player, "playing", False):
        print("\nShutting down...")


def main():
    player = None

    while True:
        timeout = 5
        timer = Timer(timeout, shutdown, [player])
        timer.start()
        rfid = input("Enter RFID: ")
        timer.cancel()

        if not rfid:
            break

        if player and rfid == player.rfid:
            print("Already playing")

        else:
            with sqlite3.connect("toem.db") as db:
                command = get_command(db, rfid)
                music_data = get_music_data(db, rfid)

            if command:
                if command == "shutdown":
                    if player:
                        player.pause()
                    break
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

    shutdown(player)


if __name__ == "__main__":
    main()
