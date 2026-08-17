import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import pytest
from unittest.mock import MagicMock, patch

import register_rfid as reg


class TestNormalizeSpotifyLocation:
    def test_plain_uri_passes_through(self):
        assert reg.normalize_spotify_location("spotify:album:abc123") \
            == "spotify:album:abc123"

    def test_copy_link_url_is_accepted(self):
        """What "Copy link" in the Spotify app actually produces"""
        url = "https://open.spotify.com/album/1chTWZhEdZvGu8sMVuZt2W?si=xYz123"
        assert reg.normalize_spotify_location(url) \
            == "spotify:album:1chTWZhEdZvGu8sMVuZt2W"

    def test_localised_url_is_accepted(self):
        url = "https://open.spotify.com/intl-de/album/1chTWZhEdZvGu8sMVuZt2W"
        assert reg.normalize_spotify_location(url) \
            == "spotify:album:1chTWZhEdZvGu8sMVuZt2W"

    def test_playlist_and_track_links(self):
        assert reg.normalize_spotify_location(
            "https://open.spotify.com/playlist/abc") == "spotify:playlist:abc"
        assert reg.normalize_spotify_location(
            "spotify:track:xyz") == "spotify:track:xyz"

    def test_uri_with_extra_segments_is_truncated(self):
        assert reg.normalize_spotify_location(
            "spotify:album:abc:extra") == "spotify:album:abc"

    @pytest.mark.parametrize("text", [
        "", "   ", None, "just some words", "https://example.com/album/abc",
        "spotify:artist",
    ])
    def test_rejects_non_locations(self, text):
        assert reg.normalize_spotify_location(text) is None


def test_format_album_title():
    album = {"name": "Der Spatz", "artists": [{"name": "Frederik Vahle"}]}
    assert reg.format_album_title(album) == "Frederik Vahle - Der Spatz"


def test_format_album_title_without_artists():
    assert reg.format_album_title({"name": "Solo"}) == "Solo"


def test_app_token_absent_without_credentials(monkeypatch):
    monkeypatch.delenv("SPOTIFY_USERCREDS", raising=False)
    assert reg.spotify_app_token() is None


def test_choose_album_from_pasted_link_fills_title(monkeypatch):
    """A pasted link should not also require typing the title"""
    album = {"name": "Der Spatz", "artists": [{"name": "Frederik Vahle"}]}
    with patch("builtins.input", side_effect=[
            "https://open.spotify.com/album/xyz?si=1"]), \
            patch.object(reg, "album_details", return_value=album):
        uri, title = reg.choose_spotify_album("tok")

    assert uri == "spotify:album:xyz"
    assert title == "Frederik Vahle - Der Spatz"


def test_choose_album_by_search(monkeypatch):
    results = [
        {"name": "Wrong One", "uri": "spotify:album:1",
         "artists": [{"name": "A"}], "total_tracks": 5, "release_date": "1999"},
        {"name": "Der Spatz", "uri": "spotify:album:2",
         "artists": [{"name": "Frederik Vahle"}], "total_tracks": 7,
         "release_date": "1982-03-05"},
    ]
    with patch("builtins.input", side_effect=["spatz vahle", "2"]), \
            patch.object(reg, "search_albums", return_value=results):
        uri, title = reg.choose_spotify_album("tok")

    assert uri == "spotify:album:2"
    assert title == "Frederik Vahle - Der Spatz"


def test_choose_album_can_be_aborted():
    with patch("builtins.input", side_effect=["q"]):
        assert reg.choose_spotify_album("tok") == (None, None)


def test_search_unavailable_without_token_still_accepts_links():
    with patch("builtins.input", side_effect=["some words",
                                              "spotify:album:abc"]):
        uri, title = reg.choose_spotify_album(None)
    assert uri == "spotify:album:abc"
    assert title is None


def test_title_input_defaults_to_suggestion():
    with patch("builtins.input", return_value=""):
        assert reg.get_title_input("Frederik Vahle - Der Spatz") \
            == "Frederik Vahle - Der Spatz"


def test_title_input_allows_override():
    with patch("builtins.input", return_value="My Own Title"):
        assert reg.get_title_input("Suggested") == "My Own Title"
