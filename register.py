#!/usr/bin/python3

import logging
import os
import sqlite3
import sys
from shutil import copy2, copytree

import env as _

logging.basicConfig(level=logging.INFO)

DATABASE_URL = os.getenv("DATABASE_URL")
MUSIC_LIBRARY = os.getenv("MUSIC_LIBRARY")

if not DATABASE_URL:
    logging.error("DATABASE_URL environment variable is not set.")
    raise ValueError("DATABASE_URL environment variable is required")


def get_db():
    """Return a connection to the SQLite database."""
    return sqlite3.connect(DATABASE_URL)


def create_tables(db):
    """Create database tables if they don't exist."""
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
    """Initialize database (create tables if they don't exist)."""
    db = get_db()
    create_tables(db)
    db.close()


def list_registered_rfids():
    """Displays all registered RFIDs and their associated URIs or album paths."""
    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT rfid, source, location FROM music")
    rows = cursor.fetchall()

    if not rows:
        print("No RFID records found.")
    else:
        for row in rows:
            print(f"RFID: {row[0]} -> Source: {row[1]}, Location: {row[2]}")
    db.close()


def register_spotify(rfid, spotify_uri):
    """Registers a new RFID card with a Spotify URI."""
    db = get_db()
    cursor = db.cursor()

    # Check if the RFID is already registered
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
        "INSERT INTO music (rfid, source, location) "
        "VALUES (?, ?, ?)",
        (rfid, "spotify", spotify_uri)
    )
    db.commit()
    print(f"Registered card {rfid} with Spotify URI: {spotify_uri}")
    db.close()


def register_local_album(rfid, album_path):
    """Registers a new RFID card with a local album and uploads it."""

    if not MUSIC_LIBRARY:
        logging.error("MUSIC_LIBRARY environment variable is not set.")
        raise ValueError("MUSIC_LIBRARY environment variable is required")

    db = get_db()
    cursor = db.cursor()

    # Check if RFID is already registered
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
            f"Album folder '{album_destination}' already exists. Choose a different album or remove the existing one.")

    try:
        if os.path.isdir(album_path):
            copytree(album_path, album_destination)
        else:
            copy2(album_path, album_destination)

        cursor.execute(
            "INSERT INTO music (rfid, source, location) "
            "VALUES (?, ?, ?)",
            (rfid, "local", album_destination)
        )
        db.commit()
        print(
            f"Registered card {rfid} with local album at: {album_destination}")
    except Exception as e:
        print(f"Error uploading album: {e}")
    db.close()


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 register_card.py <command> [options]")
        sys.exit(1)

    command = sys.argv[1]

    # Initialize the database tables if needed
    initialize_db()

    if command == "spotify":
        if len(sys.argv) != 5:
            print(
                "Usage: python3 register_card.py spotify <rfid> <spotify_uri>")
            sys.exit(1)
        rfid = sys.argv[2]
        spotify_uri = sys.argv[3]
        title = sys.argv[4]
        register_spotify(rfid, spotify_uri)

    elif command == "local":
        if len(sys.argv) != 5:
            print(
                "Usage: python3 register_card.py local <rfid> <album_path>")
            sys.exit(1)
        rfid = sys.argv[2]
        album_path = sys.argv[3]
        title = sys.argv[4]
        register_local_album(rfid, album_path)

    elif command == "list":
        list_registered_rfids()

    else:
        print("Unknown command. Available commands: spotify, local, list.")
        sys.exit(1)


if __name__ == "__main__":
    main()
