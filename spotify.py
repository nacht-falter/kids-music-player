import requests
import json
import os
import env


def spotify_auth():
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
headers = {"Authorization": f"Bearer {spotify_auth()}"}


def check_playback_status(base_url, headers):
    request_url = base_url + "/me/player/currently-playing"
    response = requests.get(request_url, headers=headers)

    if response.status_code == 200:
        return response.json()
    else:
        print(response.status_code)
        return None
