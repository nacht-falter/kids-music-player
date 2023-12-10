from threading import Timer
import sqlite3
from spotify import SpotifyPlayer
from local import AudioPlayer
import os
from playsound import playsound
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
        "shutdown": "shutdown"
    }
    playsound(f"./sounds/{sounds[event]}.wav")


def shutdown(player):
    """Shutdown computer"""
    if player and not player.playing:
        player.pause_playback()
        player.save_playback_state()
    print("\nShutting down...")
    play_sound("shutdown")


def main():
    DATABASE_URL = os.environ.get("DATABASE_URL")
    player = None
    previous_rfid = None
    play_sound("start")

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
            with sqlite3.connect(DATABASE_URL) as db:
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
                        play_sound("click")
                        play_sound("confirm_shutdown")
                        print("confirm shutdown")
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

            else:
                print("Unknown RFID")
                play_sound("error")

            previous_rfid = rfid

    shutdown(player)


if __name__ == "__main__":
    main()
