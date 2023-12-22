import sqlite3
import os


def create_tables(db):
    """Create database tables"""
    print("Creating tables...")
    cursor = db.cursor()
    cursor.execute(
        "CREATE TABLE IF NOT EXISTS commands(rfid TEXT PRIMARY KEY, "
        "command TEXT NOT NULL);"
    )
    cursor.execute(
        "CREATE TABLE IF NOT EXISTS music(rfid TEXT PRIMARY KEY, "
        "source TEXT NOT NULL, playback_state TEXT, location TEXT NOT NULL);"
    )
    cursor.execute(
        "CREATE TABLE IF NOT EXISTS last_played (last_played_rifd_id "
        "INTEGER PRIMARY KEY AUTOINCREMENT, last_played_rfid TEXT, "
        "FOREIGN KEY (last_played_rfid) REFERENCES music(rfid));"
    )
    db.commit()


def create_db(database_url):
    """Create database"""
    if not os.path.exists(database_url):
        print("Creating database...")
        db = sqlite3.connect(database_url)
        create_tables(db)
        db.close()
    else:
        print("Database exists. Skipping...")
