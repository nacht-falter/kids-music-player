import json
import os
import sqlite3
import subprocess
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
import pytest
from unittest.mock import patch

import local
from local import AudioPlayer

PLAYING = "Some Track\n[playing] #3/12   0:47/3:21 (23%)\nvolume: n/a\n"
PAUSED = "Some Track\n[paused]  #3/12   0:47/3:21 (23%)\nvolume: n/a\n"
# What `mpc status` prints once an album has run out: no state line at all.
STOPPED = "volume: n/a   repeat: off   random: off   single: off   consume: off\n"


class FakeMpc:
    """Stands in for subprocess.run, recording every mpc argv exactly

    The old tests patched os.system and counted calls, so they passed while
    every location with a space in it was split into separate words by the
    shell. Asserting on the argument list is what catches that.
    """

    def __init__(self, status=PLAYING, current="3", fail=()):
        self.calls = []
        self.status = status
        self.current = current
        self.fail = set(fail)

    def __call__(self, argv, **kwargs):
        assert isinstance(argv, list), "mpc must not go through a shell"
        assert "shell" not in kwargs
        self.calls.append(argv[1:])
        command = argv[1:][1] if argv[1:2] == ["-q"] else argv[1]
        if command in self.fail:
            return subprocess.CompletedProcess(argv, 1, "", "error: nope")
        out = {"status": self.status, "current": self.current + "\n"}.get(command, "")
        return subprocess.CompletedProcess(argv, 0, out, "")


@pytest.fixture
def fake_mpc():
    fake = FakeMpc()
    with patch("local.subprocess.run", fake):
        yield fake


def test_audioplayer_initialization():
    player = AudioPlayer("rfid1", None, "Album")
    assert player.rfid == "rfid1"
    assert player.location == "Album"
    assert player.playback_state == {"track": 1, "position": "0%"}
    assert not player.playing

    player2 = AudioPlayer("rfid2", '{"track": 2, "position": "50%"}', "Album")
    assert player2.playback_state == {"track": 2, "position": "50%"}


def test_play_passes_a_location_with_spaces_as_one_argument(fake_mpc):
    """The bug this rewrite exists for: every folder on toem2 has spaces"""
    player = AudioPlayer("rfid1", None, "Barenaked Ladies - Snacktime!")
    player.play()
    assert ["-q", "add", "Barenaked Ladies - Snacktime!"] in fake_mpc.calls
    assert player.playing


def test_play_resumes_the_saved_track_and_position(fake_mpc):
    player = AudioPlayer("rfid1", '{"track": "4", "position": "37%"}', "Album")
    player.play()
    assert fake_mpc.calls == [["-q", "clear"], ["-q", "add", "Album"],
                              ["-q", "play", "4"], ["-q", "seek", "37%"]]


def test_play_from_the_start_does_not_seek(fake_mpc):
    AudioPlayer("rfid1", None, "Album").play()
    assert fake_mpc.calls[-1] == ["-q", "play", "1"]


def test_play_with_an_empty_saved_track_starts_at_one(fake_mpc):
    """Saved while stopped by the old code, which wrote track ''"""
    AudioPlayer("rfid1", '{"track": "", "position": "0%"}', "Album").play()
    assert ["-q", "play", "1"] in fake_mpc.calls


def test_play_reports_a_location_mpd_does_not_know(fake_mpc):
    fake_mpc.fail = {"add"}
    player = AudioPlayer("rfid1", None, "Not In Library")
    with patch("utils.play_sound") as sound:
        player.play()
    sound.assert_called_once_with("playback_error")
    assert not player.playing
    assert not any(call[:2] == ["-q", "play"] for call in fake_mpc.calls)


def test_mpd_not_installed_is_a_failed_call_not_a_crash():
    with patch("local.subprocess.run", side_effect=FileNotFoundError("mpc")):
        assert local.mpc("status") is None


def test_mpc_that_hangs_is_given_up_on():
    with patch("local.subprocess.run",
               side_effect=subprocess.TimeoutExpired("mpc", 5)):
        assert local.mpc("status") is None


def test_toggle_and_pause(fake_mpc):
    player = AudioPlayer("rfid1", None, "Album")
    player.toggle_playback()
    assert player.playing
    assert fake_mpc.calls[-1] == ["-q", "toggle"]
    player.pause_playback()
    assert not player.playing
    assert fake_mpc.calls[-1] == ["-q", "pause"]


def test_restart_playback(fake_mpc):
    player = AudioPlayer("rfid1", '{"track": 3, "position": "75%"}', "Album")
    player.restart_playback()
    assert player.playback_state == {"track": 1, "position": "0%"}
    assert ["-q", "play", "1"] in fake_mpc.calls
    assert player.playing


@pytest.mark.parametrize("status, playing", [
    (PLAYING, True), (PAUSED, False), (STOPPED, False)])
def test_check_playback_status(fake_mpc, status, playing):
    fake_mpc.status = status
    player = AudioPlayer("rfid1", None, "Album")
    player.playing = not playing
    player.check_playback_status()
    assert player.playing is playing


def test_a_finished_album_stops_counting_as_playing(fake_mpc):
    """Otherwise the idle watchdog never powers the box off

    refresh_playback_state is what the watchdog calls; it used to trust the
    flag set when the card was scanned, and mpd stopping changed nothing.
    """
    player = AudioPlayer("rfid1", None, "Album")
    player.playing = True
    fake_mpc.status = STOPPED
    with patch("utils.persist_playback_state"):
        player.refresh_playback_state()
    assert not player.playing


def test_next_track(fake_mpc):
    player = AudioPlayer("rfid1", None, "Album")
    player.next_track()
    assert player.playing
    assert fake_mpc.calls[-1] == ["-q", "next"]


def test_previous_restarts_the_track_when_past_the_threshold(fake_mpc):
    """Same rule as SpotifyPlayer, via mpc"""
    AudioPlayer("rfid1", None, "Album").previous_track()
    assert fake_mpc.calls[-1] == ["-q", "seek", "0"]


def test_previous_skips_back_near_the_start(fake_mpc):
    fake_mpc.status = "Some Track\n[playing] #3/12   0:01/3:21 (0%)\n"
    AudioPlayer("rfid1", None, "Album").previous_track()
    assert fake_mpc.calls[-1] == ["-q", "prev"]


@pytest.fixture
def music_db(tmp_path, monkeypatch):
    # Must be a file, not :memory: - the state is persisted through a fresh
    # connection opened against DATABASE_URL, so it has to be reachable by path.
    database_url = str(tmp_path / "test.db")
    monkeypatch.setenv("DATABASE_URL", database_url)
    db = sqlite3.connect(database_url)
    db.execute("""CREATE TABLE music(rfid TEXT PRIMARY KEY, source TEXT NOT NULL,
                  playback_state TEXT, location TEXT NOT NULL, title TEXT)""")
    db.execute("INSERT INTO music (rfid, source, location, title) "
               "VALUES ('rfid1', 'local', 'Album', 'Test Album')")
    db.commit()
    db.close()

    def saved():
        # A separate connection: proves the write was committed, not just
        # left pending in an open transaction.
        verify = sqlite3.connect(database_url)
        row = verify.execute(
            "SELECT playback_state FROM music WHERE rfid = 'rfid1'").fetchone()
        verify.close()
        return json.loads(row[0]) if row and row[0] else None

    return saved


def test_save_playback_state(fake_mpc, music_db):
    AudioPlayer("rfid1", None, "Album").save_playback_state()
    assert music_db() == {"track": "3", "position": "23%"}


def test_a_finished_album_is_saved_to_start_over(fake_mpc, music_db):
    """mpd only stops by itself at the end; resuming there plays seconds of
    the last track and then silence"""
    fake_mpc.status = STOPPED
    fake_mpc.current = ""
    AudioPlayer("rfid1", '{"track": "12", "position": "98%"}',
                "Album").save_playback_state()
    assert music_db() == {"track": 1, "position": "0%"}
