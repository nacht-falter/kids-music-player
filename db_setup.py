import logging
import os
import sqlite3


def create_tables(db):
    """Create database tables"""
    logging.info("Creating database tables...")
    cursor = db.cursor()

    # Updated music table
    cursor.execute(
        "CREATE TABLE IF NOT EXISTS music("
        "rfid TEXT PRIMARY KEY, "
        "source TEXT NOT NULL, "
        "playback_state TEXT, "
        "location TEXT NOT NULL, "
        "title TEXT, "
        ");"
    )

    cursor.execute(
        "CREATE TABLE IF NOT EXISTS last_played ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "last_played_rfid TEXT, "
        "FOREIGN KEY (last_played_rfid) REFERENCES music(rfid)"
        ");"
    )

    db.commit()


def create_db(database_url):
    """Create database"""
    if not os.path.exists(database_url):
        logging.info("Creating database...")
        db = sqlite3.connect(database_url)
        create_tables(db)
        db.close()
    else:
        logging.warning("Database exists. Skipping...")
