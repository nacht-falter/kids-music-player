import os
import sqlite3
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import pytest
from unittest.mock import patch

import db_setup
import remote_sync


OLD = "2026-08-18 13:00:00"
LAST_SYNC = "2026-08-18 23:43:15"
NEW = "2026-08-19 08:45:00"


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = str(tmp_path / "toem.db")
    db_setup.create_db(path)
    monkeypatch.setenv("SYNC_API_URL", "https://example.invalid")
    monkeypatch.setenv("SYNC_API_TOKEN", "token")
    return path


def add_card(db_path, rfid, last_modified, title="local", source="spotify"):
    with sqlite3.connect(db_path) as db:
        db.execute(
            "INSERT INTO music (rfid, source, location, title, last_modified)"
            " VALUES (?, ?, ?, ?, ?)",
            (rfid, source, f"spotify:album:{rfid}", title, last_modified))


def set_last_sync(db_path, value):
    with sqlite3.connect(db_path) as db:
        db.execute(
            "INSERT OR REPLACE INTO sync_meta (id, last_sync) VALUES (1, ?)",
            (value,))


def remote_card(rfid, last_modified, title="remote", source="spotify"):
    return {"rfid": rfid, "source": source,
            "location": f"spotify:album:{rfid}", "title": title,
            "last_modified": last_modified}


def cards(db_path):
    with sqlite3.connect(db_path) as db:
        db.row_factory = sqlite3.Row
        return {r["rfid"]: dict(r) for r in db.execute("SELECT * FROM music")}


def run_sync(db_path, remote_items, upload_status=200):
    """Run one sync attempt with the network stubbed out."""
    with patch("remote_sync.fetch_remote_items", return_value=remote_items), \
            patch("remote_sync.requests.post") as post:
        post.return_value.status_code = upload_status
        remote_sync.sync_db(db_path, retries=1)
        return post


def test_remote_edit_of_an_unchanged_local_card_updates_it(db_path):
    """The regression: this used to INSERT a duplicate rfid and blow up

    The card exists locally but was not touched since last_sync, so it is
    absent from the changed-items delta. Checking existence against that delta
    made the code mistake it for a new card.
    """
    add_card(db_path, "0011791976", OLD, title="old title")
    set_last_sync(db_path, LAST_SYNC)

    run_sync(db_path, [remote_card("0011791976", NEW, title="new title")])

    stored = cards(db_path)
    assert len(stored) == 1, "a duplicate row was inserted"
    assert stored["0011791976"]["title"] == "new title"


def test_a_failing_card_does_not_take_the_batch_down_with_it(db_path):
    """The transaction rolled back, so new cards stayed unknown on the device

    This is the symptom the kids actually met: a freshly registered card kept
    saying "Unknown RFID" for days because an unrelated card in the same batch
    aborted every sync.
    """
    add_card(db_path, "0011791976", OLD)
    set_last_sync(db_path, LAST_SYNC)

    run_sync(db_path, [
        remote_card("0011791976", NEW),
        remote_card("0009391717", NEW, title="brand new card"),
    ])

    stored = cards(db_path)
    assert "0009391717" in stored, "the new card never landed"
    assert stored["0009391717"]["title"] == "brand new card"


def test_older_remote_edit_does_not_clobber_a_newer_local_card(db_path):
    """Existence now comes from the full table, but recency must still decide"""
    add_card(db_path, "0011791976", NEW, title="local wins")
    set_last_sync(db_path, LAST_SYNC)

    run_sync(db_path, [remote_card("0011791976", OLD, title="stale remote")])

    assert cards(db_path)["0011791976"]["title"] == "local wins"


def test_untouched_local_cards_are_not_re_uploaded(db_path):
    """Uploads still come from the delta, or `since` would buy us nothing"""
    add_card(db_path, "aaa", OLD)
    add_card(db_path, "bbb", OLD)
    add_card(db_path, "ccc", NEW, title="edited here")
    set_last_sync(db_path, LAST_SYNC)

    post = run_sync(db_path, [])

    uploaded = [item["rfid"] for item in post.call_args.kwargs["json"]]
    assert uploaded == ["ccc"]


def test_new_local_card_is_uploaded(db_path):
    add_card(db_path, "ccc", NEW)
    set_last_sync(db_path, LAST_SYNC)

    post = run_sync(db_path, [])

    assert post.call_count == 1
    assert post.call_args.kwargs["json"][0]["rfid"] == "ccc"


def test_first_sync_with_no_last_sync_inserts_everything(db_path):
    run_sync(db_path, [remote_card("aaa", NEW), remote_card("bbb", NEW)])

    assert set(cards(db_path)) == {"aaa", "bbb"}


def test_playback_state_is_not_touched_by_a_remote_update(db_path):
    """Positions are device-local; a remote edit must not reset one"""
    add_card(db_path, "0011791976", OLD)
    with sqlite3.connect(db_path) as db:
        db.execute("UPDATE music SET playback_state = ? WHERE rfid = ?",
                   ('{"position_ms": 12345}', "0011791976"))
    set_last_sync(db_path, LAST_SYNC)

    run_sync(db_path, [remote_card("0011791976", NEW, title="new title")])

    stored = cards(db_path)["0011791976"]
    assert stored["playback_state"] == '{"position_ms": 12345}'
    assert stored["title"] == "new title"


def test_failure_reason_reaches_the_error_log(db_path, caplog):
    """Otherwise a stuck device reports nothing actionable at all"""
    with patch("remote_sync.fetch_remote_items",
               side_effect=RuntimeError("boom")):
        remote_sync.sync_db(db_path, retries=1)

    assert "boom" in caplog.text
    assert "RuntimeError" in caplog.text
