from threading import Timer
import sqlite3
import spotify


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


def get_data(db, rfid):
    cursor = db.cursor()
    cursor.execute("SELECT * FROM controls where rfid = ?", (rfid,))
    command_result = cursor.fetchone()

    if command_result:
        data = {"type": "command", "command": command_result[1]}
        return data
    else:
        cursor.execute(f"SELECT * FROM music WHERE rfid = {rfid}")
        result = cursor.fetchone()
        if result:
            columns = [desc[0] for desc in cursor.description]
            data = dict(zip(columns, result))
            data["type"] = "music"
            return data
        else:
            return None


class Player:
    def __init__(self, source, playback_state, location):
        self.source = source
        self.location = location
        self.playback_state = (
            playback_state
            if playback_state
            else {"offset": 0, "position_ms": 0}
        )
        self.playing = False

    def play(self):
        if self.source == "spotify":
            spotify.play_album(
                spotify.base_url,
                spotify.headers,
                spotify.device_id,
                self.location,
                self.playback_state["offset"],
                self.playback_state["position_ms"],
            )
        else:
            pass
        self.playing = True

    def toggle_playback(self):
        if self.source == "spotify":
            spotify.toggle_playback(
                spotify.base_url, spotify.headers, spotify.device_id
            )
        self.playing = not self.playing

    def next_track(self):
        if self.source == "spotify":
            spotify.next_track(
                spotify.base_url, spotify.headers, spotify.device_id
            )

    def previous_track(self):
        if self.source == "spotify":
            spotify.previous_track(
                spotify.base_url, spotify.headers, spotify.device_id
            )

    def stop(self):
        print("Stop")
        self.playing = False


def on_timeout(player):
    shutdown(player)


def shutdown(player):
    if not getattr(player, "playing", False):
        print("\nShutting down...")


def main():
    player = None

    while True:
        timeout = 5
        timer = Timer(timeout, on_timeout, args=(player,))
        timer.start()
        rfid = input("Enter RFID: ")
        timer.cancel()

        if rfid:
            db = sqlite3.connect("toem.db")
            try:
                data = get_data(db, rfid)
                if data:
                    type = data.get("type")
                    command = data.get("command")
                    if type == "command":
                        if player:
                            if command == "shutdown":
                                player.stop()
                                shutdown(player)
                            else:
                                getattr(player, command)()
                        else:
                            if command == "shutdown":
                                shutdown(player)
                            else:
                                print("Nothing playing")
                    else:
                        player = Player(
                            data.get("source"),
                            data.get("playback_state"),
                            data.get("location"),
                        )
                        player.play()
            except Exception as e:
                print(e)


if __name__ == "__main__":
    main()
