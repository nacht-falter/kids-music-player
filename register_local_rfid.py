import logging
import os
import sqlite3

import env


def get_rfid(db):
    rfid = input("Scan RFID: ")
    if check_if_rfid_exists(db, rfid):
        logging.warning("RFID %s already exists. Please try a different one.", rfid)
        get_rfid(db)
    return rfid


def check_if_rfid_exists(db, rfid):
    cursor = db.cursor()
    rfid_music = cursor.execute(
        "SELECT * FROM music WHERE rfid=?", (rfid,)
    ).fetchone()
    rfid_commands = cursor.execute(
        "SELECT * FROM commands WHERE rfid=?", (rfid,)
    ).fetchone()

    if rfid_music or rfid_commands:
        return True
    else:
        return False


def create_database_entry(db, rfid, table, value):
    cursor = db.cursor()
    cursor.execute(
        f"INSERT INTO {table} (rfid, source, location) VALUES (?, ?, ?)",
        (rfid, "local", value),
    )
    db.commit()


def register_local_rfid(db):
    rfid = get_rfid(db)
    location = input("Enter the path to the album: ")
    create_database_entry(db, rfid, "music", location)
    logging.info("RFID %s registered.", rfid)
    return rfid


def main():
    DATABASE_URL = os.environ.get("DATABASE_URL")

    db = sqlite3.connect(DATABASE_URL)
    register_local_rfid(db)


if __name__ == "__main__":
    main()
