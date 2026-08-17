import os
import re

import requests
from tabulate import tabulate

from dotenv import load_dotenv


SPOTIFY_URL_RE = re.compile(
    r"open\.spotify\.com/(?:intl-[a-z]{2}/)?(album|playlist|track)/([A-Za-z0-9]+)")


def normalize_spotify_location(text):
    """Turn what the Spotify app actually gives you into a URI

    "Copy link" yields https://open.spotify.com/album/<id>?si=... - and may
    include an /intl-xx/ segment - none of which the player understands.
    Returns None if it is not recognisably Spotify.
    """
    text = (text or "").strip()

    if text.startswith("spotify:"):
        parts = text.split(":")
        if len(parts) >= 3 and parts[1] in ("album", "playlist", "track"):
            return ":".join(parts[:3])
        return None

    match = SPOTIFY_URL_RE.search(text)
    if match:
        return f"spotify:{match.group(1)}:{match.group(2)}"
    return None


def format_album_title(album):
    """'Artist - Album', matching how the existing cards are titled"""
    artists = ", ".join(a["name"] for a in album.get("artists", []))
    name = album.get("name", "")
    return f"{artists} - {name}" if artists else name


def spotify_app_token():
    """App-only token: catalog search needs no user scopes and no refresh token"""
    usercreds = os.environ.get("SPOTIFY_USERCREDS")
    if not usercreds:
        return None
    try:
        response = requests.post(
            "https://accounts.spotify.com/api/token",
            data={"grant_type": "client_credentials"},
            headers={"Authorization": f"Basic {usercreds}"})
        if response.status_code != 200:
            print(f"Spotify lookup unavailable: HTTP {response.status_code}")
            return None
        return response.json()["access_token"]
    except requests.RequestException as e:
        print(f"Spotify lookup unavailable: {e}")
        return None


def search_albums(token, query, limit=8, market="DE"):
    response = requests.get(
        "https://api.spotify.com/v1/search",
        params={"q": query, "type": "album", "limit": limit, "market": market},
        headers={"Authorization": "Bearer " + token})
    if response.status_code != 200:
        print(f"Search failed: HTTP {response.status_code}")
        return []
    return response.json().get("albums", {}).get("items", [])


def album_details(token, album_id, market="DE"):
    response = requests.get(
        f"https://api.spotify.com/v1/albums/{album_id}",
        params={"market": market},
        headers={"Authorization": "Bearer " + token})
    if response.status_code != 200:
        return None
    return response.json()


def choose_spotify_album(token):
    """Resolve an album by search or pasted link

    Returns (uri, suggested_title); (None, None) to abort.
    """
    while True:
        entry = input(
            "Spotify album link/URI, or words to search (q to quit): ").strip()
        if entry.lower() == "q":
            return None, None
        if not entry:
            continue

        uri = normalize_spotify_location(entry)
        if uri:
            title = None
            if token and uri.startswith("spotify:album:"):
                album = album_details(token, uri.split(":")[2])
                if album:
                    title = format_album_title(album)
                    print(f"  -> {title}")
            return uri, title

        if not token:
            print("Not a Spotify link, and search needs SPOTIFY_USERCREDS set.")
            continue

        results = search_albums(token, entry)
        if not results:
            print("Nothing found. Try other words, or paste a link.")
            continue

        for index, album in enumerate(results, 1):
            artists = ", ".join(a["name"] for a in album.get("artists", []))
            print(f"  {index}. {album['name']}  -  {artists} "
                  f"({album.get('total_tracks')} tracks, {album.get('release_date', '?')[:4]})")

        choice = input(f"Pick 1-{len(results)}, or Enter to search again: ").strip()
        if not choice.isdigit():
            continue
        index = int(choice)
        if 1 <= index <= len(results):
            album = results[index - 1]
            return album["uri"], format_album_title(album)


def list_registered_rfids(api_url, headers):
    """List registered RFID codes from the remote database """
    res = requests.get(f"{api_url}/music", headers=headers)
    if res.status_code != 200:
        print(
            f"Failed to fetch data from remote: {res.status_code} {res.text}")
        return

    items = res.json()
    if not items:
        print("No registered RFID codes found.")
        return

    table = [
        [item.get("rfid"), item.get("title"), item.get("source"),
         item.get("location"), item.get("last_modified")]
        for item in items
    ]
    headers = ["RFID", "Title", "Source", "Location", "Last Modified"]
    print(tabulate(table, headers=headers, tablefmt="simple"))


def register_rfid(api_url, headers):
    """Register or update an RFID code with music source information."""

    rfid = get_rfid_input()
    if not rfid:
        return

    if not handle_existing_rfid(api_url, headers, rfid):
        return

    source = get_source_input()
    if not source:
        return

    suggested_title = None
    if source == "spotify":
        location, suggested_title = choose_spotify_album(spotify_app_token())
    else:
        location = get_location_input(source)
    if not location:
        return

    title = get_title_input(suggested_title)
    if not title:
        return

    item = {
        "rfid": rfid,
        "source": source,
        "location": location,
        "title": title
    }

    print("\nAbout to register:")
    print(f"      card:     {rfid}")
    print(f"      title:    {title}")
    print(f"      source:   {source}")
    print(f"      location: {location}")
    if input("Save this? [Y/n]: ").strip().lower() in ("n", "no"):
        print("Cancelled, nothing was saved.")
        return

    if submit_rfid_data(api_url, headers, item):
        print(f"✓ RFID {rfid} successfully registered!")
        print("  Devices pick this up within a minute.")
    else:
        print("✗ Failed to register RFID")


def get_rfid_input():
    """Get and validate the card number

    The reader is a USB HID device: it types the digits and sends Enter, so
    scanning a card straight into this prompt fills it in and submits. Nothing
    here talks to the player - registration works with it switched off.
    """
    print("\nScan the card here, or type its number.")
    while True:
        rfid = input("Enter RFID (or 'q' to quit): ").strip()
        if rfid.lower() == 'q':
            return None
        if not rfid:
            print("RFID cannot be empty. Please try again.")
            continue
        if len(rfid) < 4:
            print("RFID seems too short. Please verify and try again.")
            continue
        return rfid


def handle_existing_rfid(api_url, headers, rfid):
    """Check if RFID exists and handle overwrite confirmation."""
    try:
        response = requests.get(f"{api_url}/music/{rfid}", headers=headers)
        if response.status_code == 200:
            existing = response.json()
            print(f"\n⚠️  RFID {rfid} is already registered:")
            print(f"      title:    {existing.get('title')}")
            print(f"      source:   {existing.get('source')}")
            print(f"      location: {existing.get('location')}")
            while True:
                overwrite = input(
                    "Overwrite existing entry? [y/N]: ").strip().lower()
                if overwrite in ('n', 'no', ''):
                    print("Operation cancelled.")
                    return False
                elif overwrite in ('y', 'yes'):
                    return True
                else:
                    print("Please enter 'y' for yes or 'n' for no.")
        elif response.status_code == 404:
            return True  # RFID doesn't exist, proceed
        else:
            print(f"Error checking existing RFID: {response.status_code}")
            return False
    except requests.RequestException as e:
        print(f"Network error: {e}")
        return False


def get_source_input():
    """Get and validate source input."""
    print("\nSelect source:")
    print("  s) Spotify")
    print("  l) Local files")

    while True:
        source = input("Enter choice [s/l] (or 'q' to quit): ").strip().lower()
        if source == 'q':
            return None
        elif source in ('s', 'spotify'):
            return 'spotify'
        elif source in ('l', 'local'):
            return 'local'
        else:
            print("Invalid choice. Please enter 's' for Spotify or 'l' for local.")


def get_location_input(source):
    """Get location input based on source type."""
    if source == 'spotify':
        message = "Enter Spotify URI (e.g., spotify:album:abc123): "
    else:
        message = "Enter album/folder name: "

    while True:
        location = input(message).strip()
        if not location:
            if input("Location cannot be empty. Continue anyway? [y/N]: ").strip().lower() == 'y':
                return location
            continue

        # Validate based on source type
        if source == 'spotify':
            if not location.startswith('spotify:') or len(location.split(':')) < 3:
                print(
                    "Invalid Spotify URI format. Should be like 'spotify:album:abc123'")
                continue

        return location


def get_title_input(suggested=None):
    """Get title input, defaulting to the one looked up from Spotify."""
    prompt = ("Enter display title [%s]: " % suggested) if suggested \
        else "Enter display title: "
    while True:
        title = input(prompt).strip()
        if not title and suggested:
            return suggested
        if not title:
            if input("Title cannot be empty. Continue anyway? [y/N]: ").strip().lower() == 'y':
                return title
            continue
        return title


def submit_rfid_data(api_url, headers, item):
    """Submit RFID data to the API."""
    try:
        response = requests.post(
            f"{api_url}/music/upsert", json=item, headers=headers)
        if response.status_code == 200:
            return True
        else:
            print(f"API Error {response.status_code}: {response.text}")
            return False
    except requests.RequestException as e:
        print(f"Network error: {e}")
        return False


def delete_rfid(api_url, headers):
    """Delete specified RFID from database with confirmation"""
    rfid = get_rfid_input()

    try:
        res = requests.get(f"{api_url}/music/{rfid}", headers=headers)
        if res.status_code == 404:
            print(f"RFID {rfid} not found in the database.")
            return
        elif res.status_code != 200:
            print(f"API Error {res.status_code}: {res.text}")
            return

        item = res.json()
        print("RFID entry found:")
        for key, value in item.items():
            print(f"  {key}: {value}")

        confirm = input(
            "Are you sure you want to delete this RFID? [y/N]: ").strip().lower()
        if confirm != "y":
            print("Deletion cancelled.")
            return

    except requests.RequestException as e:
        print(f"Network error while fetching RFID: {e}")
        return

    try:
        response = requests.delete(f"{api_url}/music/{rfid}", headers=headers)
        if response.status_code == 200:
            print(f"RFID {rfid} successfully removed from database.")
        else:
            print(f"API Error {response.status_code}: {response.text}")
    except requests.RequestException as e:
        print(f"Network error during deletion: {e}")


def main():
    # Loaded here rather than at import time: doing it on import mutates
    # os.environ for anything that imports this module, including the test
    # suite, where a stray DEVELOPMENT=true sends utils.shutdown() down the
    # os._exit() branch and kills the test runner mid-run.
    load_dotenv()

    API_URL = os.environ.get("SYNC_API_URL")
    API_TOKEN = os.environ.get("SYNC_API_TOKEN")

    if not API_URL:
        print("Error: SYNC_API_URL environment variable is required.")
        return

    if not API_TOKEN:
        print("Warning: SYNC_API_TOKEN environment variable not set.")

    headers = {"Authorization": f"Bearer {API_TOKEN}"} if API_TOKEN else {}

    while True:
        print("\n" + "="*50)
        print("Register or list RFID codes")
        print("="*50)
        print("Commands:")
        print("  (a)dd      - Register new RFID tag")
        print("  (d)elete   - Delete RFID")
        print("  (l)ist     - List existing entries")
        print("  (q)uit     - Exit program")
        print("-"*50)

        command = input("Enter command: ").strip().lower()

        if command in ("a", "add"):
            register_rfid(API_URL, headers)
        elif command in ("l", "list"):
            list_registered_rfids(API_URL, headers)
        elif command in ("d", "delete"):
            delete_rfid(API_URL, headers)
        elif command in ("q", "quit", "exit"):
            print("Exiting ...")
            break
        else:
            print(f"Unknown command: '{command}'")
            print("Please use 'a' for add, 'l' for list, or 'q' to quit.")

        answer = input("\nContinue?").strip().lower()
        if not answer in ("", "y", "yes"):
            print("Exiting ...")
            break


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nProgram interrupted. Goodbye!")
    except Exception as e:
        print(f"Unexpected error: {e}")
        import traceback
        traceback.print_exc()
