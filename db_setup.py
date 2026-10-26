import logging
import os
import sqlite3


def create_tables(db):
    """Create database tables"""
    logging.info("Creating database tables...")
    cursor = db.cursor()

    # Updated music table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS music(
        rfid TEXT PRIMARY KEY,
        source TEXT NOT NULL,
        playback_state TEXT,
        location TEXT NOT NULL,
        title TEXT,
        last_modified TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS last_played (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        last_played_rfid TEXT,
        FOREIGN KEY (last_played_rfid) REFERENCES music(rfid)
        );
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sync_meta (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        last_sync TIMESTAMP
    );
    """)

    create_play_time_table(db)

    db.commit()


def create_play_time_table(db):
    """Seconds of playback per local calendar day

    Separate from create_tables because existing devices never run that:
    create_db only fires when the database file is missing, so the player
    creates this one itself before its first write.
    """
    db.execute("""
        CREATE TABLE IF NOT EXISTS play_time (
        day TEXT PRIMARY KEY,
        seconds INTEGER NOT NULL DEFAULT 0
        );
    """)


def create_db(database_url):
    """Create database"""
    if not os.path.exists(database_url):
        logging.info("Creating database...")
        db = sqlite3.connect(database_url)
        create_tables(db)
        db.close()
    else:
        logging.warning("Database exists. Skipping...")
