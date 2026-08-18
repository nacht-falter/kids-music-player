"""Series cards: a playlist of episodes, one album each"""
import json
import os
import sys
import threading

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import pytest
from unittest.mock import MagicMock, patch

import spotify


EPISODES = [
    {"uri": "spotify:album:ep1", "title": "Folge 1", "durations": [60000, 90000]},
    {"uri": "spotify:album:ep2", "title": "Folge 2", "durations": [70000]},
    {"uri": "spotify:album:ep3", "title": "Folge 3", "durations": [50000, 50000]},
]

PLAYLIST = "spotify:playlist:series1"


def make_player(state=None, episodes=None):
    """A series player with its episode map stubbed out"""
    with patch.object(spotify.SpotifySeriesPlayer, "_load_episodes",
                      return_value=episodes if episodes is not None else EPISODES):
        return spotify.SpotifySeriesPlayer(
            "rfid1", json.dumps(state) if state else None, PLAYLIST)


# --- building the episode map -----------------------------------------------

def _page(items, nxt=None):
    response = MagicMock(status_code=200)
    response.json.return_value = {"items": items, "next": nxt}
    response.raise_for_status.return_value = None
    return response


def _track(album_id, name="Folge", duration=1000):
    return {"track": {"uri": f"spotify:track:{album_id}x",
                      "duration_ms": duration,
                      "album": {"uri": f"spotify:album:{album_id}",
                                "name": name}}}


class TestEpisodeMap:
    def _fetch(self, pages):
        with patch.object(spotify.SpotifySeriesPlayer, "_load_episodes",
                          return_value=[]):
            player = spotify.SpotifySeriesPlayer("rfid1", None, PLAYLIST)
        with patch("spotify.requests.get", side_effect=pages):
            return player._fetch_episodes()

    def test_consecutive_tracks_of_one_album_are_one_episode(self):
        episodes = self._fetch([_page([_track("a"), _track("a"), _track("b")])])
        assert [e["uri"] for e in episodes] == ["spotify:album:a",
                                                "spotify:album:b"]
        assert len(episodes[0]["durations"]) == 2

    def test_a_revisited_album_is_a_separate_episode(self):
        """Runs, not grouping: order in the playlist is what the child hears"""
        episodes = self._fetch([_page([_track("a"), _track("b"), _track("a")])])
        assert [e["uri"] for e in episodes] == ["spotify:album:a",
                                                "spotify:album:b",
                                                "spotify:album:a"]

    def test_entries_without_an_album_are_skipped(self):
        """Removed tracks, local files and podcast episodes arrive like this"""
        episodes = self._fetch([_page([{"track": None},
                                       {"track": {"album": None}},
                                       _track("a")])])
        assert [e["uri"] for e in episodes] == ["spotify:album:a"]

    def test_pagination_is_followed(self):
        """The playlist paging path had never run against the real API"""
        episodes = self._fetch([
            _page([_track("a")], nxt="more"),
            _page([_track("b")]),
        ])
        assert [e["uri"] for e in episodes] == ["spotify:album:a",
                                                "spotify:album:b"]

    def test_durations_are_recorded_for_finished_detection(self):
        episodes = self._fetch([_page([_track("a", duration=1234)])])
        assert episodes[0]["durations"] == [1234]


# --- where we are in the series ---------------------------------------------

class TestEpisodePosition:
    def test_a_fresh_card_starts_at_the_first_episode(self):
        assert make_player().episode_index == 0

    def test_a_stored_episode_is_restored(self):
        assert make_player({"episode": 1, "offset": {"position": 0},
                            "position_ms": 0}).episode_index == 1

    def test_an_out_of_range_episode_falls_back_to_the_first(self):
        """The playlist may have been shortened since the last scan"""
        player = make_player({"episode": 99, "offset": {"position": 0},
                              "position_ms": 0})
        assert player.episode_index == 0

    def test_unfinished_episode_is_resumed_not_advanced(self):
        player = make_player({"episode": 0, "offset": {"position": 0},
                              "position_ms": 10000})
        assert player.episode_index == 0
        assert player.playback_state["position_ms"] == 10000

    def test_finished_episode_advances_and_resets_position(self):
        """On the last track, at its end -> the next scan starts episode 2"""
        player = make_player({"episode": 0, "offset": {"position": 1},
                              "position_ms": 90000})
        assert player.episode_index == 1
        assert player.playback_state["offset"]["position"] == 0
        assert player.playback_state["position_ms"] == 0

    def test_last_track_but_not_near_the_end_does_not_advance(self):
        player = make_player({"episode": 0, "offset": {"position": 1},
                              "position_ms": 1000})
        assert player.episode_index == 0

    def test_finishing_the_last_episode_wraps_to_the_first(self):
        player = make_player({"episode": 2, "offset": {"position": 1},
                              "position_ms": 50000})
        assert player.episode_index == 0

    def test_a_series_with_no_episodes_does_not_crash(self):
        """An unreadable playlist must not take the card down with it"""
        player = make_player(episodes=[])
        assert player.episode_index == 0
        assert player.current_episode() is None


# --- overrides that make playback work --------------------------------------

class TestPlaybackContext:
    def test_context_is_the_current_episode_album(self):
        player = make_player({"episode": 1, "offset": {"position": 0},
                              "position_ms": 0})
        assert player._context_uri() == "spotify:album:ep2"

    def test_context_falls_back_to_the_playlist_when_unknown(self):
        assert make_player(episodes=[])._context_uri() == PLAYLIST

    def test_every_episode_counts_as_our_own_playback(self):
        player = make_player()
        player.device_id = "dev1"
        for episode in EPISODES:
            playback = {"device": {"id": "dev1"},
                        "context": {"uri": episode["uri"]}}
            assert player.owns_playback(playback) is True

    def test_foreign_content_is_still_refused(self):
        """The invariant that stops a phone's podcast being adopted"""
        player = make_player()
        player.device_id = "dev1"
        assert player.owns_playback(
            {"device": {"id": "dev1"},
             "context": {"uri": "spotify:show:podcast"}}) is False

    def test_another_device_is_still_refused(self):
        player = make_player()
        player.device_id = "dev1"
        assert player.owns_playback(
            {"device": {"id": "phone"},
             "context": {"uri": "spotify:album:ep1"}}) is False


class TestEpisodeSurvivesPersistence:
    """Both save paths rebuild the state dict and would drop the episode"""

    def test_snapshot_keeps_the_episode(self):
        player = make_player({"episode": 1, "offset": {"position": 0},
                              "position_ms": 0})
        state = player._state_from_playback({
            "item": {"duration_ms": 70000, "track_number": 1},
            "context": {"uri": "spotify:album:ep2"},
            "progress_ms": 5000,
        })
        assert state["episode"] == 1

    def test_persist_keeps_the_episode(self):
        player = make_player({"episode": 2, "offset": {"position": 0},
                              "position_ms": 0})
        with patch("spotify.utils.persist_playback_state") as persist:
            player._persist_state({"offset": {"position": 0},
                                   "position_ms": 42})
        assert persist.call_args.args[1]["episode"] == 2


class TestEpisodeNavigation:
    def test_next_episode_advances_and_plays(self):
        player = make_player()
        with patch.object(player, "play") as play:
            player.next_episode()
        assert player.episode_index == 1
        play.assert_called_once()

    def test_previous_episode_wraps_backwards(self):
        player = make_player()
        with patch.object(player, "play"):
            player.previous_episode()
        assert player.episode_index == len(EPISODES) - 1

    def test_changing_episode_resets_the_position(self):
        player = make_player({"episode": 0, "offset": {"position": 1},
                              "position_ms": 30000})
        with patch.object(player, "play"):
            player.next_episode()
        assert player.playback_state == {"episode": 1,
                                         "offset": {"position": 0},
                                         "position_ms": 0}
