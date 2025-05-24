import os

import requests
from tabulate import tabulate

import env as _


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

    location = get_location_input(source)
    if not location:
        return

    title = get_title_input()
    if not title:
        return

    item = {
        "rfid": rfid,
        "source": source,
        "location": location,
        "title": title
    }

    if submit_rfid_data(api_url, headers, item):
        print(f"✓ RFID {rfid} successfully registered!")
    else:
        print("✗ Failed to register RFID")


def get_rfid_input():
    """Get and validate RFID input."""
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
            print(f"⚠️  RFID {rfid} already exists.")
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


def get_title_input():
    """Get title input."""
    while True:
        title = input("Enter display title: ").strip()
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
