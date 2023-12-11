from threading import Timer
import sqlite3
import os
from playsound import playsound

from spotify import SpotifyPlayer
from local import AudioPlayer
import register_rfid
import db_setup
import env


def get_command(db, rfid):
    """Get command to execute from database"""
    cursor = db.cursor()
    cursor.execute("SELECT * FROM commands where rfid = ?", (rfid,))
    command = cursor.fetchone()

    if command:
        return command[1]
    else:
        return None


def get_music_data(db, rfid):
    """Get music data from database"""
    cursor = db.cursor()
    cursor.execute("SELECT * FROM music WHERE rfid = ?", (rfid,))
    result = cursor.fetchone()
    if result:
        columns = [desc[0] for desc in cursor.description]
        data = dict(zip(columns, result))
        return data
    else:
        return None


def create_player(music_data):
    """Create audio player instance"""
    if music_data["source"] == "spotify":
        player = SpotifyPlayer(
            music_data["rfid"],
            music_data["playback_state"],
            music_data["location"],
        )
    else:
        player = AudioPlayer(
            music_data["rfid"],
            music_data["playback_state"],
            music_data["location"],
        )

    return player


def play_sound(event):
    """Play sound file associated with event"""
    sounds = {
        "start": "start",
        "confirm": "confirm",
        "error": "error",
        "next_track": "click",
        "previous_track": "click",
        "toggle_playback": "click",
        "confirm_shutdown": "confirm_shutdown",
        "shutdown": "shutdown",
    }
    playsound(f"./sounds/{sounds[event]}.wav")


def save_last_played(db, rfid):
    """Save last played album to database"""
    cursor = db.cursor()
    cursor.execute("DELETE FROM last_played")
    cursor.execute(
        "INSERT INTO last_played (last_played_rfid) VALUES (?)", (rfid,)
    )
    db.commit()


def get_last_played(db):
    """Get last played album from database"""
    cursor = db.cursor()
    cursor.execute("SELECT last_played_rfid FROM last_played")
    result = cursor.fetchone()
    if result:
        return result[0]
        print(result[0])
    else:
        return None


def shutdown(player):
    """Shutdown computer"""
    if player and not player.playing:
        player.pause_playback()
        player.save_playback_state()
    print("\nShutting down...")
    play_sound("shutdown")


def main():
    # Prepare database:
    DATABASE_URL = os.environ.get("DATABASE_URL")

    if not os.path.exists(DATABASE_URL):
        db_setup.create_db(DATABASE_URL)

    db = sqlite3.connect(DATABASE_URL)

    # Check if command RFIDs are registered
    register_rfid.register_commands(db)

    player = None
    previous_rfid = None
    play_sound("start")

    # Get last played album and load player
    last_played = get_last_played(db)
    if last_played:
        print(last_played)
        music_data = get_music_data(db, last_played)
        player = create_player(music_data)
        print(player.rfid)
        print(player.location)

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
            play_sound("confirm")
            if player.playing:
                player.restart_playback()
            else:
                player.toggle_playback()

        else:
            # Get command and music data from database
            command = get_command(db, rfid)
            music_data = get_music_data(db, rfid)

            # Execute command or play music
            if command:
                if command == "shutdown":
                    if rfid == previous_rfid:
                        if player:
                            player.pause_playback()
                        break
                    else:
                        play_sound("confirm_shutdown")
                        print("confirm shutdown")

                elif command == "register_rfid":
                    if player:
                        player.pause_playback()
                    register_rfid.register_spotify_rfid(db)

                else:
                    if player:
                        play_sound(command)
                        getattr(player, command)()
                    else:
                        play_sound("error")
                        print("No player found")

            elif music_data:
                if player:
                    player.pause_playback()
                    player.save_playback_state()
                play_sound("confirm")
                player = create_player(music_data)
                player.play()

                save_last_played(db, music_data["rfid"])

            else:
                print("Unknown RFID")
                play_sound("error")

            previous_rfid = rfid

    db.close()
    shutdown(player)


if __name__ == "__main__":
    main()
