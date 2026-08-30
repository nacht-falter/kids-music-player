#!/usr/bin/env python3
"""Re-authorize Spotify and install a fresh refresh token.

Spotify expires refresh tokens six months after authorization, so this has to
be done periodically. Doing it by hand is slow and has a trap: a token issued
to the wrong Spotify account authenticates perfectly and only fails later, with
an empty device list and cards that silently do nothing. This script refuses to
install a token until it has checked for that.

Usage:
    python reauth.py --host toem2       # read and update .env on toem2 over ssh
    python reauth.py                    # operate on ./.env
    python reauth.py --host toem2 --dry-run
"""

import argparse
import base64
import datetime
import json
import re
import subprocess
import sys
import urllib.parse

import requests

REDIRECT_URI = "https://johannesbernet.com/spotify/callback"
SCOPES = [
    "user-read-playback-state",
    "user-modify-playback-state",
    "user-read-currently-playing",
    "user-read-playback-position",
    "playlist-read-private",
]
# spotifyd 0.4.x prefers the oauth blob at startup, then the zeroconf one; the
# flat file is what 0.3.x used and 0.4.x ignores. Checking only the flat file
# means validating a token against credentials spotifyd will not use.
SPOTIFYD_BLOBS = (
    ("oauth", "~/.cache/spotifyd/oauth/credentials.json"),
    ("zeroconf", "~/.cache/spotifyd/zeroconf/credentials.json"),
    ("cached", "~/.cache/spotifyd/credentials.json"),
)
SPOTIFYD_CREDENTIALS = "~/.cache/spotifyd/credentials.json"
AUTHENTICATED_AS = re.compile(r"Authenticated as '([^']+)'")


def run(host, command):
    """Run a shell command locally or on a remote host"""
    if host:
        command = ["ssh", "-o", "BatchMode=yes", host, command]
    else:
        command = ["bash", "-c", command]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"command failed on {host or 'localhost'}: {result.stderr.strip()}")
    return result.stdout


def read_env(host, env_path):
    """Parse the target's .env into a dict

    Values may be quoted. A target whose player sources the file with the shell
    rather than reading it in Python has it written the way a shell script
    would write it. Accept both, or the base64 credentials come back with the
    quotes still attached and fail to decode.
    """
    text = run(host, f"cat {env_path}")
    env = {}
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            env[key.strip()] = value
    return env


def blob_account(host, path):
    """The account named by one of spotifyd's credential files, or None"""
    try:
        return json.loads(run(host, f"cat {path}")).get("username")
    except Exception:  # absent, unreadable, or not JSON - all mean "no answer"
        return None


def journal_account(host):
    """Who spotifyd is logged in as right now, from its journal, or None

    The credential files say what the *next* start would use; the journal says
    who is actually logged in, and those diverge exactly when it matters - a
    Connect takeover switches the running session before any file changes.
    """
    try:
        raw = run(host, "journalctl -u spotifyd --no-pager -o cat -n 500 "
                        "2>/dev/null || true")
    except Exception:
        return None
    found = AUTHENTICATED_AS.findall(raw or "")
    return found[-1] if found else None


def spotifyd_account(host, credentials=None):
    """The Spotify account spotifyd is logged in as, if we can determine it

    The token must belong to this account. A token for a different account
    authenticates fine but sees a different device list, so the player finds no
    device and every card silently fails.

    Asks the journal first, then the credential files in spotifyd 0.4.x's own
    precedence order. *credentials* overrides all of it with a single path, for
    a layout none of this describes.

    Returns (account, source). Both are None when nothing could be read.
    """
    if credentials:
        return blob_account(host, credentials), credentials

    running = journal_account(host)
    if running:
        return running, "journal (logged in now)"

    for name, path in SPOTIFYD_BLOBS:
        account = blob_account(host, path)
        if account:
            return account, f"{name} blob"

    print("  ! could not determine spotifyd's account from the journal or "
          "any credential file")
    return None, None


def disagreeing_blobs(host, expected):
    """Credential files naming an account other than *expected*

    A blob holding a different account is what makes a Connect takeover
    survive a reboot, so it is worth naming even when the running session is
    correct.
    """
    return [(name, account)
            for name, path in SPOTIFYD_BLOBS
            for account in [blob_account(host, path)]
            if account and account != expected]


def build_authorize_url(client_id):
    params = {
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,
        "scope": " ".join(SCOPES),
        # Force the account chooser; otherwise an already-signed-in browser
        # silently reuses whichever account it has.
        "show_dialog": "true",
    }
    return "https://accounts.spotify.com/authorize?" + urllib.parse.urlencode(params)


def extract_code(pasted):
    """Pull the auth code out of whatever the user pasted

    Accepts a bare code, or the full redirect URL. The callback appends a `ubi`
    parameter, and the browser may land on a different host than the registered
    redirect_uri, so neither can be assumed.
    """
    pasted = pasted.strip()
    if not pasted:
        raise ValueError("nothing pasted")

    if "code=" in pasted:
        query = urllib.parse.urlparse(pasted).query or pasted
        values = urllib.parse.parse_qs(query).get("code")
        if not values:
            raise ValueError("no code parameter found")
        return values[0]

    # A bare code, possibly with a stray "&ubi=..." tail attached.
    return pasted.split("&")[0]


def exchange(code, usercreds):
    response = requests.post(
        "https://accounts.spotify.com/api/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            # Must match the authorize request exactly, even though the browser
            # may have been redirected elsewhere by the time you see it.
            "redirect_uri": REDIRECT_URI,
        },
        headers={"Authorization": f"Basic {usercreds}"},
    )
    if response.status_code != 200:
        raise RuntimeError(
            f"token exchange failed: HTTP {response.status_code} "
            f"{response.text.strip()}")
    return response.json()


def validate(access_token, expected_account, expected_device_id):
    """Check the token is usable before it replaces a working one

    Returns a list of problems; empty means good.
    """
    problems = []
    headers = {"Authorization": "Bearer " + access_token}

    me = requests.get("https://api.spotify.com/v1/me", headers=headers)
    if me.status_code != 200:
        return [f"/v1/me returned HTTP {me.status_code}"]
    account = me.json().get("id")
    print(f"  token belongs to: {account}")

    if expected_account and account != expected_account:
        problems.append(
            f"wrong account: token is for {account!r}, but spotifyd is logged "
            f"in as {expected_account!r}. Authorization succeeds either way, "
            f"but the player would see no device.")

    devices = requests.get(
        "https://api.spotify.com/v1/me/player/devices", headers=headers)
    if devices.status_code != 200:
        problems.append(f"/v1/me/player/devices returned HTTP {devices.status_code}")
        return problems

    found = devices.json().get("devices", [])
    print(f"  visible devices: {len(found)}")
    for device in found:
        marker = "  <-- configured" if device["id"] == expected_device_id else ""
        print(f"    {device['name']!r} ({device['type']}){marker}")

    if expected_device_id and not any(
            d["id"] == expected_device_id for d in found):
        problems.append(
            f"configured SPOTIFY_DEVICE_ID {expected_device_id} is not in the "
            f"device list. Is spotifyd running on the target?")

    return problems


def update_env(host, env_path, values):
    """Rewrite the given keys in the target's .env, backing it up first"""
    stamp = datetime.date.today().strftime("%Y%m%d")
    run(host, f"cp -n {env_path} {env_path}.bak-{stamp} || true")

    payload = base64.b64encode(json.dumps(values).encode()).decode()
    script = (
        "import base64, json, sys\n"
        f"values = json.loads(base64.b64decode('{payload}'))\n"
        f"path = '{env_path}'\n"
        "lines = open(path).read().splitlines()\n"
        "seen = set()\n"
        "out = []\n"
        # Keep whatever quoting the line already had: where the .env is sourced
        # by a shell, silently dropping the quotes would change how that file
        # parses.
        "def requote(old, value):\n"
        "    old = old.split('=', 1)[1].strip()\n"
        "    if len(old) >= 2 and old[0] == old[-1] and old[0] in '\\\"\\'':\n"
        "        return old[0] + value + old[0]\n"
        "    return value\n"
        "for line in lines:\n"
        "    key = line.split('=', 1)[0].strip() if '=' in line else None\n"
        "    if key in values:\n"
        "        out.append(key + '=' + requote(line, values[key]))\n"
        "        seen.add(key)\n"
        "    else:\n"
        "        out.append(line)\n"
        "for key, value in values.items():\n"
        "    if key not in seen:\n"
        "        out.append(key + '=' + value)\n"
        "open(path, 'w').write('\\n'.join(out) + '\\n')\n"
    )
    encoded = base64.b64encode(script.encode()).decode()
    run(host, f"python3 -c \"import base64;exec(base64.b64decode('{encoded}'))\"")
    return f"{env_path}.bak-{stamp}"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", help="ssh host to operate on (default: local)")
    parser.add_argument("--env-path", default=None,
                        help="path to .env on the target")
    parser.add_argument("--service", default="toem",
                        help="systemd service to restart (default: toem)")
    parser.add_argument("--spotifyd-credentials", default=None,
                        help="read the account from this one credential file "
                             "instead of asking the journal and spotifyd's "
                             "own oauth/zeroconf/cached precedence")
    parser.add_argument("--restart-command", default=None,
                        help="shell command that restarts the player, instead "
                             "of restarting --service. For targets with no "
                             "systemd unit, where picking up a new token means "
                             "restarting something else, or rebooting.")
    parser.add_argument("--dry-run", action="store_true",
                        help="validate but do not write or restart")
    args = parser.parse_args()

    env_path = args.env_path or ("/home/pi/toem/.env" if args.host else ".env")
    target = args.host or "localhost"

    print(f"Target: {target}  ({env_path})")
    env = read_env(args.host, env_path)

    usercreds = env.get("SPOTIFY_USERCREDS")
    if not usercreds:
        sys.exit("SPOTIFY_USERCREDS missing from the target .env")
    try:
        client_id = base64.b64decode(usercreds).decode().split(":")[0]
    except Exception:
        sys.exit("SPOTIFY_USERCREDS is not valid base64 of 'client_id:secret'")

    expected_account, source = spotifyd_account(
        args.host, args.spotifyd_credentials)
    if expected_account:
        print(f"spotifyd account: {expected_account}  (from {source})")
        # A blob naming someone else is how a Connect takeover survives a
        # reboot: the running session is fine, the next start is not.
        for name, other in disagreeing_blobs(args.host, expected_account):
            print(f"  ! {name} blob names {other}, not {expected_account} - "
                  f"a restart would log spotifyd in as {other}")
    else:
        print("spotifyd account: unknown - the account check will be skipped")

    print("\nOpen this URL, in a private window or after signing out, and")
    print(f"sign in as {expected_account or 'the account spotifyd uses'}:\n")
    print(build_authorize_url(client_id))
    print("\nThen paste the code, or the whole redirect URL, below.")

    try:
        pasted = input("code/url> ")
    except (EOFError, KeyboardInterrupt):
        sys.exit("\naborted")

    code = extract_code(pasted)
    print(f"\nExchanging code ({len(code)} chars) ...")
    token = exchange(code, usercreds)
    print(f"  scopes granted: {token.get('scope')}")

    print("\nValidating before installing ...")
    problems = validate(token["access_token"], expected_account,
                        env.get("SPOTIFY_DEVICE_ID"))
    if problems:
        print("\nREFUSING to install this token:")
        for problem in problems:
            print(f"  - {problem}")
        sys.exit(1)
    print("  all checks passed")

    if args.dry_run:
        print("\n--dry-run: not writing anything")
        return

    backup = update_env(args.host, env_path, {
        "SPOTIFY_REFRESH_TOKEN": token["refresh_token"],
        # Spotify never tells us when a token was issued, so record it here.
        # That is the only way to warn before the six months are up.
        "SPOTIFY_AUTH_DATE": datetime.date.today().isoformat(),
    })
    print(f"\nWrote {env_path} (backup: {backup})")

    expiry = datetime.date.today() + datetime.timedelta(days=183)
    print(f"This token should last until roughly {expiry.isoformat()}.")

    if args.restart_command:
        if args.host:
            run(args.host, args.restart_command)
            print(f"Ran: {args.restart_command}")
        else:
            print(f"Run `{args.restart_command}` to pick up the new token.")
    elif args.host:
        run(args.host, f"sudo systemctl restart {args.service}")
        state = run(args.host, f"systemctl is-active {args.service}").strip()
        print(f"Restarted {args.service}: {state}")
    else:
        print(f"Restart {args.service} to pick up the new token.")


if __name__ == "__main__":
    main()
