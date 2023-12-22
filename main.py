import sqlite3
import os
from playsound import playsound
import time
import threading
from gpiozero import Button

from spotify import SpotifyPlayer, get_spotify_auth_token
from local import AudioPlayer
import register_rfid
import db_setup
import led
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


def create_player(spotify_auth_token, music_data):
    """Create audio player instance"""
    if music_data["source"] == "spotify":
        player = SpotifyPlayer(
            spotify_auth_token,
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

    sound_folder = os.path.dirname(os.path.abspath(__file__)) + "/sounds/"

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
    playsound(f"{sound_folder}{sounds[event]}.wav")


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
    else:
        return None


def handle_already_playing(player):
    """Handle already playing album"""
    if player.playing:
        player.restart_playback()
    else:
        player.toggle_playback()


def handle_register_rfid_command(command, player, db):
    """Handle register RFID command"""
    if command == "register_rfid":
        if player:
            player.pause_playback()
        register_rfid.register_spotify_rfid(db)


def handle_other_commands(command, player):
    """Handle all other commands"""
    if command != "register_rfid":
        if player:
            play_sound(command)
            getattr(player, command)()
        else:
            play_sound("error")


def shutdown(player):
    """Shutdown computer"""
    play_sound("shutdown")
    if player:
        player.save_playback_state()
    led.turn_on_led(14)
    led.turn_off_led(23)
    if os.environ.get("DEVELOPMENT") == "False":
        print("\nShutting down...")
        os.system("sudo shutdown -h now")


def check_playback_status(player):
    while True:
        if player:
            player.check_playback_status()
            time.sleep(10)


class ButtonHandler:
    def __init__(self, player):
        self.player = player
        self.last_button = None
        self.consecutive_presses = 0

        # Set up buttons with callbacks
        self.button_3 = Button(3)
        self.button_3.when_pressed = lambda: self.handle_buttons("shutdown")

        self.button_17 = Button(17)
        self.button_17.when_pressed = lambda: self.handle_buttons(
            "toggle_playback"
        )

        self.button_27 = Button(27)
        self.button_27.when_pressed = lambda: self.handle_buttons("next_track")

        self.button_22 = Button(22)
        self.button_22.when_pressed = lambda: self.handle_buttons(
            "previous_track"
        )

    def handle_buttons(self, button):
        if self.last_button == button:
            self.consecutive_presses += 1
        else:
            self.last_button = button
            self.consecutive_presses = 1

        if button == "shutdown":
            if self.consecutive_presses == 1:
                play_sound("confirm_shutdown")
            elif self.consecutive_presses == 2:
                self.consecutive_presses = 0
                shutdown(self.player)

        elif button == "toggle_playback":
            if self.player:
                play_sound(button)
                self.player.toggle_playback()
            else:
                play_sound("error")

        elif button == "next_track":
            if self.player:
                play_sound(button)
                self.player.next_track()
            else:
                play_sound("error")

        elif button == "previous_track":
            if self.player:
                play_sound(button)
                self.player.previous_track()
            else:
                play_sound("error")


def main():
    # Prepare database:
    DATABASE_URL = os.environ.get("DATABASE_URL")

    if not os.path.exists(DATABASE_URL):
        db_setup.create_db(DATABASE_URL)

    db = sqlite3.connect(DATABASE_URL)

    # get spotify auth token
    spotify_auth_token = get_spotify_auth_token()

    # Check if command RFIDs are registered
    register_rfid.register_commands(db)

    player = None

    # Get last played album and load player
    last_played = get_last_played(db)
    if last_played:
        music_data = get_music_data(db, last_played)
        player = create_player(spotify_auth_token, music_data)

    # create button handler
    button_handler = ButtonHandler(player)

    playback_status_thread = threading.Thread(
        target=check_playback_status, args=(player,)
    )
    playback_status_thread.start()

    play_sound("start")

    # Handle LED
    led.turn_off_led(14)
    led.turn_on_led(23)

    while True:
        # Wait for RFID input
        timeout = 3600
        timer = threading.Timer(timeout, shutdown, [player])
        timer.start()
        rfid = input("Enter RFID: ")
        timer.cancel()

        if not rfid:
            break

        # Check if RFID is already playing
        if player and rfid == player.rfid:
            play_sound("confirm")
            handle_already_playing(player)

        else:
            # Get command and music data from database
            command = get_command(db, rfid)
            music_data = get_music_data(db, rfid)

            # Execute command or play music
            if command:
                led.flash_led(23)
                handle_register_rfid_command(command, player, db)
                handle_other_commands(command, player)

            elif music_data:
                led.flash_led(23)
                play_sound("confirm")
                if player:
                    player.pause_playback()
                    player.save_playback_state()
                player = create_player(spotify_auth_token, music_data)
                player.play()
                button_handler.player = player

                save_last_played(db, music_data["rfid"])

            else:
                print("Unknown RFID")
                play_sound("error")


if __name__ == "__main__":
    main()
