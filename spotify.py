import requests
import json
import os
import env


def spotify_auth():
    """Get Spotify API access token"""
    USERCREDS = os.environ.get("USERCREDS")
    REFRESH_TOKEN = os.environ.get("REFRESH_TOKEN")
    token_url = "https://accounts.spotify.com/api/token"
    token_data = {
        "grant_type": "refresh_token",
        "refresh_token": REFRESH_TOKEN,
    }
    token_headers = {
        "Authorization": f"Basic {USERCREDS}",
    }
    response = requests.post(token_url, data=token_data, headers=token_headers)
    token_response_data = response.json()
    if response.status_code == 200:
        print("Token refreshed")
        return token_response_data["access_token"]
    else:
        print(response.status_code)
        return None


base_url = "https://api.spotify.com/v1"
eaders = {"Authorization": f"Bearer {spotify_auth()}"}
device_id = os.environ.get("DEVICE_ID")


def check_playback_status(base_url, headers):
    """Check if Spotify is playing"""
    request_url = base_url + "/me/player/currently-playing"
    response = requests.get(request_url, headers=headers)

    if response.status_code == 200:
        return response.json()
    else:
        print(response.status_code)
        return None


def play(base_url, headers, device_id, uri, offset, position_ms):
    """Play Spotify album"""
    request_url = base_url + "/me/player/play?device_id=" + device_id
    data = {
        "context_uri": uri,
        "offset": {"position": offset},
        "position_ms": position_ms,
    }
    response = requests.put(request_url, headers=headers, json=data)

    if response.status_code != 204:
        print(response.status_code)


def toggle_playback(base_url, headers, device_id):
    """Toggle Spotify playback"""
    if check_playback_status(base_url, headers).get("is_playing"):
        request_url = base_url + "/me/player/pause?device_id=" + device_id
    else:
        request_url = base_url + "/me/player/play?device_id=" + device_id
    response = requests.put(request_url, headers=headers)

    if response.status_code != 204:
        print(response.status_code)


def pause_playback(base_url, headers, device_id):
    """Pause Spotify playback"""
    request_url = base_url + "/me/player/pause?device_id=" + device_id
    response = requests.put(request_url, headers=headers)
    if response.status_code != 204:
        print(response.status_code)


def next_track(base_url, headers, device_id):
    """Play next track"""
    request_url = base_url + "/me/player/next?device_id=" + device_id
    response = requests.post(request_url, headers=headers)
    if response.status_code != 204:
        print(response.status_code)


def previous_track(base_url, headers, device_id):
    """Play previous track"""
    request_url = base_url + "/me/player/previous?device_id=" + device_id
    response = requests.post(request_url, headers=headers)
    if response.status_code != 204:
        print(response.status_code)
