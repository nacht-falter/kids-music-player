#!/usr/bin/python3

import glob
import logging
import os
import readline
import sqlite3
from shutil import copy2, copytree

import env as _

logging.basicConfig(level=logging.INFO)

DATABASE_URL = os.getenv("DATABASE_URL")
MUSIC_LIBRARY = os.getenv("MUSIC_LIBRARY")

if not DATABASE_URL:
    logging.error("DATABASE_URL environment variable is not set.")
    raise ValueError("DATABASE_URL environment variable is required")


def get_db():
    return sqlite3.connect(DATABASE_URL)


def create_tables(db):
    logging.info("Creating database tables...")
    cursor = db.cursor()
    cursor.execute(
        "CREATE TABLE IF NOT EXISTS music(rfid TEXT PRIMARY KEY, "
        "source TEXT NOT NULL, playback_state TEXT, location TEXT NOT NULL, title TEXT);"
    )
    cursor.execute(
        "CREATE TABLE IF NOT EXISTS last_played (last_played_rifd_id "
        "INTEGER PRIMARY KEY AUTOINCREMENT, last_played_rfid TEXT, "
        "FOREIGN KEY (last_played_rfid) REFERENCES music(rfid));"
    )
    db.commit()


def initialize_db():
    db = get_db()
    create_tables(db)
    db.close()


def list_registered_rfids():
    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT rfid, source, location, title FROM music")
    rows = cursor.fetchall()

    if not rows:
        print("No RFID records found.")
    else:
        for row in rows:
            print(
                f"RFID: {row[0]} -> Source: {row[1]}, Location: {row[2]}, Title: {row[3]}")
    db.close()


def complete_path(text, state):
    line = readline.get_line_buffer().split()
    return [x for x in glob.glob(text + '*')][state]


def update_mpd():
    if not os.system("mpc status") and not os.system("systemctl is-active --quiet mpd"):
        print("Updating database...")
        os.system("mpc update")
    else:
        print("MPD is not available.")


def register_spotify():
    rfid = input("Enter RFID: ").strip()
    spotify_uri = input("Enter Spotify URI: ").strip()
    title = input("Enter title: ").strip()

    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT location FROM music WHERE rfid=?", (rfid,))
    existing = cursor.fetchone()

    if existing:
        response = input(
            f"Card {rfid} already exists. Overwrite? [y/N]: ").strip().lower()
        if response != 'y':
            print("Aborted.")
            db.close()
            return
        cursor.execute("DELETE FROM music WHERE rfid=?", (rfid,))

    cursor.execute(
        "INSERT INTO music (rfid, source, location, title) "
        "VALUES (?, ?, ?, ?)",
        (rfid, "spotify", spotify_uri, title)
    )
    db.commit()
    print(f"Registered card {rfid} with Spotify URI: {spotify_uri}")
    db.close()


def register_local_album():
    if not MUSIC_LIBRARY:
        logging.error("MUSIC_LIBRARY environment variable is not set.")
        raise ValueError("MUSIC_LIBRARY environment variable is required")

    rfid = input("Enter RFID: ").strip()

    # Enable tab directory completion
    readline.set_completer_delims(' \t\n;')
    readline.parse_and_bind("tab: complete")
    readline.set_completer(complete_path)

    album_path = input("Enter path to album: ").strip()

    # Disable tab directory completion
    readline.set_completer(None)

    title = input("Enter album title: ").strip()

    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT location FROM music WHERE rfid=?", (rfid,))
    existing = cursor.fetchone()

    if existing:
        response = input(
            f"Card {rfid} already exists. Overwrite? [y/N]: ").strip().lower()
        if response != 'y':
            print("Aborted.")
            db.close()
            return
        cursor.execute("DELETE FROM music WHERE rfid=?", (rfid,))

    album = os.path.basename(os.path.normpath(album_path))
    album_destination = os.path.join(MUSIC_LIBRARY, album)

    if os.path.exists(album_destination):
        raise FileExistsError(
            f"Album folder '{album_destination}' already exists. Choose a different album or remove the existing one."
        )

    try:
        if os.path.isdir(album_path):
            copytree(album_path, album_destination)
        else:
            copy2(album_path, album_destination)

        cursor.execute(
            "INSERT INTO music (rfid, source, location, title) "
            "VALUES (?, ?, ?, ?)",
            (rfid, "local", album_destination, title)
        )
        db.commit()
        print(
            f"Registered card {rfid} with local album at: {album_destination}")

        if input("Update mpd database? [y/N]: ").strip().lower() in ("y", "yes"):
            update_mpd()

    except Exception as e:
        print(f"Error uploading album: {e}")
    db.close()


def main():
    initialize_db()
    while True:
        print("\nChoose a command: spotify, local, list, quit")
        command = input("> ").strip().lower()

        if command == "spotify":
            register_spotify()
        elif command == "local":
            register_local_album()
        elif command == "list":
            list_registered_rfids()
        elif command in ("quit", "exit"):
            print("Bye!")
            break
        else:
            print("Unknown command. Try: spotify, local, list, quit.")


if __name__ == "__main__":
    main()
