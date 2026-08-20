import json
import logging
import os
import sqlite3
import threading
import time

import requests


def fetch_remote_items(api_url, headers, last_sync):
    params = {"since": last_sync} if last_sync else {}
    r = requests.get(f"{api_url}/music", headers=headers, params=params)
    if r.status_code != 200:
        raise RuntimeError(
            f"Failed to fetch remote changes: {r.status_code} {r.text}")
    return r.json()


def fetch_local_items(cursor, last_sync):
    """Local rows changed since last_sync - i.e. upload candidates.

    Only ever use this to decide what to push. Deciding whether a remote row
    already exists needs fetch_all_local_items(); see the comment in sync_db.
    """
    if last_sync:
        cursor.execute(
            "SELECT * FROM music WHERE last_modified > ?", (last_sync,))
    else:
        cursor.execute("SELECT * FROM music")
    return [dict(row) for row in cursor.fetchall()]


def fetch_all_local_items(cursor):
    """Every local row, regardless of when it last changed."""
    cursor.execute("SELECT * FROM music")
    return [dict(row) for row in cursor.fetchall()]


def sync_db(database_url, sync_done=None, retries=5, delay=5):
    API_URL = os.environ.get("SYNC_API_URL")
    API_TOKEN = os.environ.get("SYNC_API_TOKEN", "")

    if not API_URL:
        logging.error("API_URL environment variable is not set.")
        raise ValueError("API_URL environment variable is required.")

    headers = {"Authorization": f"Bearer {API_TOKEN}"}

    if sync_done:
        sync_done.clear()

    success = False
    last_error = None

    for attempt in range(retries):
        try:
            with sqlite3.connect(database_url) as db:
                db.row_factory = sqlite3.Row
                cursor = db.cursor()

                cursor.execute(
                    "SELECT last_sync FROM sync_meta WHERE id = 1")
                row = cursor.fetchone()
                last_sync = row["last_sync"] if row else None
                logging.debug(f"Last sync: {last_sync}")

                remote_items = fetch_remote_items(
                    API_URL, headers, last_sync)

                remote_map = {item["rfid"]: item for item in remote_items}
                local_items = fetch_local_items(cursor, last_sync)
                local_map = {item["rfid"]: item for item in local_items}
                # Existence must be checked against the whole table, not the
                # delta. A card edited on the server but untouched here is
                # absent from local_map, so the loop below took it for a new
                # card and INSERTed a duplicate rfid. That raised "UNIQUE
                # constraint failed", rolled the transaction back, and took
                # every genuinely new card in the same batch down with it - so
                # freshly registered cards stayed unknown on the device
                # indefinitely, with only "All database sync attempts failed"
                # to show for it.
                existing_map = {item["rfid"]: item
                                for item in fetch_all_local_items(cursor)}

                for rfid, remote_item in remote_map.items():
                    local_item = existing_map.get(rfid)
                    if not local_item:
                        cursor.execute("""
                            INSERT INTO music (rfid, source, location, title, last_modified)
                            VALUES (?, ?, ?, ?, ?)
                        """, (
                            rfid,
                            remote_item["source"],
                            remote_item["location"],
                            remote_item["title"],
                            remote_item["last_modified"]
                        ))
                    elif remote_item["last_modified"] > local_item["last_modified"]:
                        cursor.execute("""
                            UPDATE music SET source=?, location=?, title=?, last_modified=?
                            WHERE rfid=?
                        """, (
                            remote_item["source"],
                            remote_item["location"],
                            remote_item["title"],
                            remote_item["last_modified"],
                            rfid
                        ))

                upload_items = []
                for rfid, local_item in local_map.items():
                    remote_item = remote_map.get(rfid)
                    if not remote_item or local_item["last_modified"] > remote_item["last_modified"]:
                        upload_items.append(local_item)

                if upload_items:
                    sync_res = requests.post(
                        f"{API_URL}/music/sync", json=upload_items, headers=headers)
                    if sync_res.status_code != 200:
                        raise RuntimeError(
                            "Failed to sync items to remote database.")

                if remote_items or upload_items:
                    cursor.execute(
                        "INSERT OR REPLACE INTO sync_meta (id, last_sync) VALUES (1, CURRENT_TIMESTAMP)"
                    )
                    db.commit()
                    logging.info("Sync complete.")
                    if remote_items:
                        logging.debug(
                            f"Items synced from remote: {json.dumps(remote_items, indent=4)}")
                    if upload_items:
                        logging.debug(
                            f"Items synced to remote: {json.dumps(upload_items, indent=4)}")
                else:
                    logging.info("Nothing to sync.")

                success = True
                break
        except Exception as e:
            last_error = e
            logging.debug(f"Sync attempt {attempt + 1} failed: {e}")
            if attempt < retries - 1:
                logging.debug(f"Retrying in {delay} seconds...")
                time.sleep(delay)
    if sync_done:
        sync_done.set()

    if not success:
        # The reason used to be DEBUG-only, so a device left syncing into a
        # UNIQUE constraint failure for days reported nothing but this one
        # unactionable line. Always say why.
        logging.error(
            "All database sync attempts failed. Last error: %s: %s",
            type(last_error).__name__, last_error)


def schedule_sync(database_url, sync_done=None, retries=5, delay=5, interval=900):
    def sync_loop():
        while True:
            sync_db(database_url, sync_done, retries, delay)
            logging.debug(f"Next sync in {interval} seconds.")
            time.sleep(interval)

    thread = threading.Thread(target=sync_loop, daemon=True)
    thread.start()
