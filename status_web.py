"""A status page the player serves about itself.

The device has no screen, so a card that does nothing leaves nothing to look
at. This serves one page on the device's own address that says, in plain
language, whether Spotify still works, what is actually playing and what the
player has been doing - so that a photo of it is a usable bug report.

Built after the same page on the legacy Kidsbox answered "why did playback
stop" twice in one evening without anyone opening a terminal.

Three rules it keeps:

- **It asks the players, not us.** "What is playing" goes to mpd and to
  Spotify, never to `player.playing` - our own state is exactly what is wrong
  when something is wrong, and a phone that took the session leaves it wrong
  silently.
- **It changes nothing.** Every handler is read-only. It opens its own
  short-lived database connection rather than borrowing the main thread's,
  because sqlite3 objects raise when used from another thread - a bug this
  project has already had once.
- **It never hangs.** Every shell command has a timeout and every request a
  deadline; a diagnostic page that hangs is worse than no page.

It shows spotifyd's journal for a reason our own log cannot cover: a dropped
Spotify session happens entirely inside spotifyd and never reaches this
process. `spotifyd is active` is not a health check either - it can sit
authenticated-less for half an hour while systemd calls it running - so the
row that matters is whether our device id appears in Spotify's device list.

Runs on a daemon thread inside the player (`status_web.start(app)`), which is
what gives it the live card, the idle countdown and the hardware state. It can
also be run on its own (`python status_web.py`) for a device whose player is
not running; everything the app would have supplied is then simply left out.
"""

import datetime
import html
import json
import logging
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import requests

import spotify

DEFAULT_PORT = 8080
LOG_LINES = 30
JOURNAL_LINES = 8
API = "https://api.spotify.com/v1"
API_TIMEOUT = 8
# Spelled out rather than expanded from "~": spotifyd runs as pi, this page
# runs inside the player, which runs as root, so "~" is /root and none of
# these are under it.
SPOTIFYD_CACHE = os.environ.get("SPOTIFYD_CACHE", "/home/pi/.cache/spotifyd")
SPOTIFYD_CREDENTIALS = os.path.join(SPOTIFYD_CACHE, "credentials.json")
# 0.4.x keeps two more, and prefers them in this order. The oauth blob is the
# only one a Connect takeover cannot rewrite.
SPOTIFYD_OAUTH_BLOB = os.path.join(SPOTIFYD_CACHE, "oauth/credentials.json")
SPOTIFYD_ZEROCONF_CREDENTIALS = os.path.join(
    SPOTIFYD_CACHE, "zeroconf/credentials.json")

# Dot colours. Green and red are claims; grey is deliberately common, because
# most rows are facts rather than verdicts and a page of coloured dots stops
# meaning anything.
OK, WARN, BAD, FLAT = "ok", "warn", "bad", "flat"

_started_at = time.monotonic()


def sh(command, timeout=4):
    """Best-effort shell one-liner, empty string on any failure"""
    try:
        out = subprocess.run(command, shell=True, timeout=timeout,
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        return out.stdout.decode(errors="replace").strip()
    except Exception:
        return ""


def db_query(database_url, sql, params=()):
    """Read from the player's database on a connection of our own

    Never the app's connection. It belongs to the thread that opened it, and
    sqlite3 raises "SQLite objects created in a thread can only be used in
    that same thread" otherwise - which is how a button press used to lose the
    saved position. Read-only queries only, so a short-lived connection costs
    nothing.
    """
    if not database_url or not os.path.exists(database_url):
        return None
    try:
        db = sqlite3.connect(database_url, timeout=2)
    except sqlite3.Error:
        return None
    try:
        return db.execute(sql, params).fetchall()
    except sqlite3.Error:
        return None
    finally:
        db.close()


# --------------------------------------------------------------------------
# Spotify
# --------------------------------------------------------------------------

def api_get(token, path, timeout=API_TIMEOUT):
    response = requests.get(API + path,
                            headers={"Authorization": "Bearer " + token},
                            timeout=timeout)
    if response.status_code == 204:  # nothing playing
        return None
    response.raise_for_status()
    return response.json()


def spotify_snapshot():
    """Everything the page needs from Spotify, fetched once

    Three sections ask about the token, the device list and current playback,
    and each of those is a network round trip.

    Asking the auth manager for a token is the one piece of shared state this
    page touches. That is deliberate: it is the same call every API request
    makes, so its answer is the honest one, and a token refreshed here is a
    refresh the player does not have to do next.
    """
    snap = {"token": None, "token_error": None,
            "devices": None, "devices_error": None,
            "playback": None, "playback_error": None,
            "me": None, "me_error": None}

    try:
        manager = spotify.get_auth_manager()
        snap["token"] = manager.get_token()
        if not snap["token"]:
            snap["token_error"] = (
                "Spotify rejected the credentials; the player is waiting "
                "before it asks again"
                if manager.rejected_at else "could not get an access token")
    except Exception as error:  # missing credentials, network, bad response
        snap["token_error"] = str(error)

    if not snap["token"]:
        return snap

    try:
        snap["devices"] = (api_get(snap["token"], "/me/player/devices")
                           or {}).get("devices", [])
    except Exception as error:
        snap["devices_error"] = str(error)

    try:
        snap["playback"] = api_get(snap["token"], "/me/player")
    except Exception as error:
        snap["playback_error"] = str(error)

    # Whose token this is. Only interesting next to the account spotifyd is
    # logged in as: when those two differ, nothing works and nothing says why.
    try:
        snap["me"] = api_get(snap["token"], "/me")
    except Exception as error:
        snap["me_error"] = str(error)

    return snap


def this_device(devices):
    """Our own entry in Spotify's device list, or None"""
    wanted = os.environ.get("SPOTIFY_DEVICE_ID")
    for device in devices or []:
        if device.get("id") == wanted:
            return device
    return None


def spotifyd_account():
    """The Spotify account spotifyd is logged in as, if it can be determined

    The player's refresh token has to belong to this account. One for a
    different account authenticates perfectly and then sees a device list
    without our device in it, so every card fails silently. toem and toem2 are
    on separate accounts, which makes that an easy mistake to make and an
    invisible one afterwards.

    Asked of the journal, not of a credential file. Any Spotify client on the
    network can log spotifyd into another account over Connect, and on 0.4.x
    the file this used to read (`~/.cache/spotifyd/credentials.json`) is no
    longer consulted at all - so on 2026-08-29 it would have reported the
    right account for three hours while the device sat on the wrong one.
    """
    logged_in = sh("journalctl -u spotifyd --no-pager -o cat -n 500 "
                   "| grep -o \"Authenticated as '[^']*'\" | tail -n 1",
                   timeout=8)
    if logged_in:
        return logged_in.split("'")[1]

    # No journal (or none since the last boot): fall back to the credential
    # files, newest precedence first. These say what the next start would use,
    # which is a guess at the present, not a reading of it.
    for path in (SPOTIFYD_OAUTH_BLOB, SPOTIFYD_ZEROCONF_CREDENTIALS,
                 os.environ.get("SPOTIFYD_CREDENTIALS", SPOTIFYD_CREDENTIALS)):
        try:
            with open(os.path.expanduser(path), encoding="utf-8") as handle:
                return json.load(handle).get("username")
        except Exception:  # never run, cache cleared, unreadable JSON
            continue
    return None


def token_expiry_row():
    """When the refresh token runs out, from the date reauth.py recorded

    Spotify never reports when a token was issued, so an install that predates
    SPOTIFY_AUTH_DATE has nothing to check. Unknown is reported as unknown, not
    as expired.
    """
    raw = os.environ.get("SPOTIFY_AUTH_DATE", "").strip()
    if not raw:
        return ("Spotify login expires",
                "unknown - not recorded before the last reauthorization", FLAT)
    try:
        issued = datetime.date.fromisoformat(raw)
    except ValueError:
        return ("Spotify login expires", "unreadable date: " + raw, WARN)

    expires = issued + datetime.timedelta(
        days=spotify.REFRESH_TOKEN_LIFETIME_DAYS)
    left = (expires - datetime.date.today()).days
    shown = expires.strftime("%d %b %Y")
    if left < 0:
        return ("Spotify login expires",
                "%s - expired %d days ago" % (shown, -left), BAD)
    return ("Spotify login expires", "%s (%d days left)" % (shown, left),
            OK if left > spotify.EXPIRY_WARNING_DAYS else WARN)


def spotify_rows(snap):
    rows = []

    if snap["token"]:
        rows.append(("Spotify connection", "working", OK))
    else:
        rows.append(("Spotify connection", "not working - reauthorization "
                     "needed", BAD))
        if snap["token_error"]:
            rows.append(("Reported by Spotify", snap["token_error"], FLAT))

    rows.append(token_expiry_row())

    account = spotifyd_account()
    ours = (snap.get("me") or {}).get("id")
    if account and ours and account != ours:
        # Someone played to this box from a phone signed into another account.
        # Spotify Connect lets any client on the network do that, and it
        # survives a reboot, so this is the row that explains a box which has
        # stopped answering to every card at once.
        rows.append(("Account spotifyd uses",
                     "%s - taken over; our token is %s" % (account, ours),
                     BAD))
    else:
        rows.append(("Account spotifyd uses", account or "unknown",
                     OK if account else WARN))

    if snap["token"]:
        # The health check that counts. `systemctl is-active spotifyd` says
        # running while spotifyd sits at "Connecting to AP" forever, with no
        # device registered and every card failing.
        if snap["devices_error"]:
            rows.append(("Visible to Spotify",
                         "could not ask (%s)" % snap["devices_error"], WARN))
        else:
            here = this_device(snap["devices"])
            if here:
                rows.append(("Visible to Spotify",
                             "yes, as '%s'" % here.get("name"), OK))
                percent = here.get("volume_percent")
                if percent is not None:
                    rows.append(("Spotify volume", "%d %%" % percent,
                                 WARN if percent < 20 else FLAT))
            else:
                others = len(snap["devices"] or [])
                rows.append(("Visible to Spotify",
                             "no - %d other device%s visible instead"
                             % (others, "s" if others != 1 else "") if others
                             else "no - Spotify sees no devices at all", BAD))
    return rows


# --------------------------------------------------------------------------
# Playback
# --------------------------------------------------------------------------

SPOTIFYD_COMPLAINTS = ("unable to|failed|error|panic|"
                       "reset by peer|subscription terminated")


def spotifyd_error_lines(limit=JOURNAL_LINES):
    """Recent spotifyd complaints, read straight from the journal

    Our own log cannot see these: a dropped session, a broken audio-key
    channel or an AP connect that never completes all happen inside spotifyd
    and only reach us as "the device is not there".

    -t matches the syslog identifier, so this works whether spotifyd runs as a
    user unit or a system one. Where the journal is volatile it covers the
    current boot only.
    """
    raw = sh("journalctl -t spotifyd --no-pager -o short-precise "
             "| grep -iE '%s' | tail -n %d" % (SPOTIFYD_COMPLAINTS, limit),
             timeout=8)
    lines = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        # "Aug 20 02:15:06.123456 toem spotifyd[620]: Connecting to AP ..."
        message = line.split("]: ", 1)[-1]
        parts = line.split()
        stamp = parts[2].split(".")[0] if len(parts) > 2 else ""
        lines.append("%s  %s" % (stamp, message) if stamp else message)
    return lines


def now_playing_row(snap):
    """What is actually coming out of the speaker, asked of the players

    Never from our own state. `player.playing` is a cache updated every
    STATE_REFRESH_INTERVAL and left wrong by anything that happens elsewhere -
    which is the situation this page usually gets opened in.
    """
    mpd = sh("mpc status")
    if "[playing]" in mpd:
        return ("Playing now", "local: " + mpd.splitlines()[0], OK)

    playback = snap["playback"]
    if playback and playback.get("is_playing"):
        item = playback.get("item") or {}
        artists = ", ".join(a.get("name", "") for a in item.get("artists") or [])
        title = " - ".join(part for part in (artists, item.get("name")) if part)
        device = playback.get("device") or {}
        if device.get("id") == os.environ.get("SPOTIFY_DEVICE_ID"):
            return ("Playing now", "Spotify: " + (title or "unknown"), OK)
        # Playing on the account, but somewhere else. Worth saying, because it
        # is why the device can look idle while Spotify insists it is busy -
        # and why the buttons refuse to work.
        return ("Playing now",
                "nothing here - Spotify is on '%s'"
                % device.get("name", "another device"), FLAT)

    return ("Playing now", "nothing", FLAT)


def format_ms(milliseconds):
    seconds = int(milliseconds or 0) // 1000
    return "%d:%02d" % (seconds // 60, seconds % 60)


def card_title(app, rfid):
    rows = db_query(app.database_url if app else None,
                    "SELECT title FROM music WHERE rfid = ?", (rfid,))
    return rows[0][0] if rows else None


def loaded_card_rows(app, snap):
    """The card in the player right now, and where it thinks it is

    This is the half the Kidsbox page could not have: the position, the
    episode and the card are in memory here, not in a file that goes stale.
    """
    rows = []
    # Read the player without taking player_lock. The lock is held for the
    # whole of a card scan, Spotify round trips included, so waiting for it
    # would stall the page for seconds to avoid a reading that is at worst a
    # moment out of date.
    player = app.get_player() if app else None
    if player is None:
        rows.append(("Card loaded", "none since the player started", FLAT))
        return rows

    title = card_title(app, player.rfid) or "unknown album"
    rows.append(("Card loaded", "%s (%s)" % (title, player.rfid), FLAT))

    if getattr(player, "is_series", False):
        episode = player.current_episode()
        rows.append(("Episode", "%d of %d%s" % (
            player.episode_index + 1, len(player.episodes),
            " - " + episode["title"] if episode and episode.get("title") else ""),
            FLAT))

    state = getattr(player, "playback_state", None) or {}
    if "position_ms" in state:
        track = (state.get("offset") or {}).get("position", 0) + 1
        rows.append(("Saved position", "track %d at %s"
                     % (track, format_ms(state["position_ms"])), FLAT))
    elif "position" in state:  # local files: mpd's own track and percentage
        rows.append(("Saved position", "track %s at %s"
                     % (state.get("track") or "?", state["position"] or "?"), FLAT))

    playback = snap["playback"]
    device = (playback or {}).get("device") or {}
    if playback and device.get("id") != os.environ.get("SPOTIFY_DEVICE_ID"):
        # The buttons decline to act on someone else's session rather than
        # skipping tracks on a phone, so they look broken until it is handed
        # back. Say so before anyone opens the log.
        rows.append(("Spotify session",
                     "held by '%s' - the buttons will not act on it"
                     % device.get("name", "another device"), WARN))
    return rows


def playback_rows(app, snap):
    rows = [now_playing_row(snap)]
    rows.extend(loaded_card_rows(app, snap))

    # pgrep rather than systemctl: spotifyd is a *user* unit, so
    # `systemctl --user` answers only for the user that asks, and the player
    # may well be running as root.
    running = bool(sh("pgrep -x spotifyd | head -1"))
    rows.append(("Spotify service (spotifyd)", "running" if running
                 else "not running", OK if running else BAD))

    mpd_state = sh("systemctl is-active mpd") or "unknown"
    rows.append(("Local playback (mpd)",
                 "running" if mpd_state == "active" else mpd_state,
                 OK if mpd_state == "active" else WARN))

    complaints = spotifyd_error_lines()
    rows.append(("spotifyd complaints",
                 "none since boot" if not complaints
                 else "%d since boot" % len(complaints),
                 OK if not complaints else WARN))
    return rows


# --------------------------------------------------------------------------
# The player itself
# --------------------------------------------------------------------------

def player_rows(app):
    rows = []
    if app is None:
        rows.append(("Player program", "not running - this page is being "
                     "served on its own", BAD))
    else:
        rows.append(("Player program", "running for %s"
                     % format_duration(time.monotonic() - _started_at), OK))

        with app.activity_lock:
            idle_for = time.monotonic() - app.last_activity
        left = max(0, app.idle_time - idle_for)
        if os.getenv("DEVELOPMENT", "").lower() == "true":
            # The watchdog calls os._exit(0) in this mode, and exit 0 is not a
            # failure, so Restart=on-failure leaves the service stopped while
            # the Pi stays powered on: alive, silent, ignoring every card.
            note = ("in %s the player stops (DEVELOPMENT=true, so the Pi "
                    "stays on)" % format_duration(left))
        else:
            note = "in %s the device switches itself off" % format_duration(left)
        rows.append(("Idle timeout", note, FLAT))

        rows.append(("Card reader", "ready" if app.rfid_reader
                     else "not found - cards cannot be read",
                     OK if app.rfid_reader else BAD))

        handler = os.getenv("BUTTON_HANDLER", "gpio")
        rows.append(("Buttons (%s)" % handler,
                     "ready" if app.button_handler else "not available",
                     OK if app.button_handler else WARN))

        # Asked of sys.modules rather than by importing led here: importing it
        # configures GPIO pins as a side effect, and the import is the check -
        # it fails on a venv built without --system-site-packages, which is
        # exactly how the LED once went dead with nothing in the log.
        led_ok = sys.modules.get("led") is not None
        rows.append(("Status LED", "ready" if led_ok
                     else "unavailable (venv without --system-site-packages?)",
                     OK if led_ok else WARN))

    rows.extend(card_rows(app))
    return rows


def card_rows(app):
    """What the card database holds, and whether it is still being updated"""
    database_url = (app.database_url if app
                    else os.environ.get("DATABASE_URL"))
    rows = []

    counted = db_query(database_url,
                       "SELECT source, COUNT(*) FROM music GROUP BY source")
    if counted is None:
        rows.append(("Cards registered", "database unreadable", BAD))
        return rows

    total = sum(count for _, count in counted)
    detail = ", ".join("%d %s" % (count, source) for source, count in counted)
    rows.append(("Cards registered",
                 "%d%s" % (total, " (%s)" % detail if detail else ""),
                 FLAT if total else WARN))

    last = db_query(database_url,
                    "SELECT m.title, m.rfid FROM last_played l "
                    "LEFT JOIN music m ON m.rfid = l.last_played_rfid LIMIT 1")
    if last:
        title, rfid = last[0]
        rows.append(("Last card played", title or rfid or "unknown", FLAT))

    if os.environ.get("ENABLE_SYNC", "").lower() == "true":
        synced = db_query(database_url,
                          "SELECT last_sync FROM sync_meta WHERE id = 1")
        stamp = synced[0][0] if synced and synced[0][0] else None
        rows.append(("New cards synced", stamp or "never since setup",
                     OK if stamp else WARN))
    else:
        rows.append(("New cards synced", "sync is switched off", FLAT))

    return rows


# --------------------------------------------------------------------------
# The Pi
# --------------------------------------------------------------------------

def format_duration(seconds):
    seconds = int(seconds)
    days, rest = divmod(seconds, 86400)
    hours, rest = divmod(rest, 3600)
    minutes = rest // 60
    if days:
        return "%d days %d h" % (days, hours)
    if hours:
        return "%d h %d min" % (hours, minutes)
    if minutes:
        return "%d min" % minutes
    return "%d s" % seconds


def device_rows():
    rows = []

    address = ""
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.connect(("192.0.2.1", 1))  # never sent, just picks the route
        address = probe.getsockname()[0]
        probe.close()
    except Exception:
        pass
    rows.append(("Address on the network", address or "unknown",
                 OK if address else BAD))

    ssid = sh("iwgetid -r")
    if ssid:
        # /proc/net/wireless reports link quality out of 70 on this adapter.
        quality = sh("awk 'NR==3 {print int($3)}' /proc/net/wireless")
        state, note = OK, ssid
        try:
            percent = max(0, min(100, int(round(int(quality) * 100 / 70.0))))
            note = "%s (%d %%)" % (ssid, percent)
            state = OK if percent >= 40 else WARN
        except ValueError:
            pass
        rows.append(("Wifi", note, state))
    else:
        rows.append(("Wifi", "not connected", WARN))

    try:
        with open("/proc/uptime") as handle:
            rows.append(("Pi running for",
                         format_duration(float(handle.read().split()[0])),
                         FLAT))
    except Exception:
        pass

    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as handle:
            celsius = int(handle.read().strip()) / 1000.0
        rows.append(("Temperature", "%.0f °C" % celsius,
                     OK if celsius < 70 else WARN))
    except Exception:
        pass

    # Bit 0 is undervoltage right now, bit 16 undervoltage since boot: the
    # classic cause of a Pi that drops wifi, corrupts its card or reboots under
    # load. Both devices have thousands of these events on record.
    throttled = sh("vcgencmd get_throttled")
    if "=" in throttled:
        try:
            bits = int(throttled.split("=")[1], 16)
        except ValueError:
            bits = None
        if bits is not None:
            if bits & 0x1:
                rows.append(("Power supply",
                             "too little power, right now - try another "
                             "supply or cable", BAD))
            elif bits & 0x10000:
                rows.append(("Power supply",
                             "dipped too low at least once since boot", WARN))
            else:
                rows.append(("Power supply", "fine", OK))

    try:
        free_mb = shutil.disk_usage("/").free // (1024 * 1024)
        shown = ("%.1f GB" % (free_mb / 1024.0) if free_mb >= 1024
                 else "%d MB" % free_mb)
        rows.append(("Disk space free", shown, OK if free_mb > 300 else WARN))
    except Exception:
        pass

    return rows


# --------------------------------------------------------------------------
# The log
# --------------------------------------------------------------------------

def log_path(app):
    """Where main.py put the log, or the same guess main.py would make"""
    if app is not None and getattr(app, "log_path", None):
        return app.log_path
    app_dir = os.path.dirname(os.path.abspath(__file__))
    name = os.getenv("APP_NAME", "rfid_music_player").lower()
    return os.path.join(app_dir, "%s.log" % name)


def recent_log(app, limit=LOG_LINES):
    """The last few lines of the player's log, DEBUG left out

    DEVELOPMENT=true puts the root logger at DEBUG, where urllib3 logs every
    connection it opens - including the ones this page makes. Left in, a
    refresh would fill the tail with the page's own requests to Spotify and
    push out the card scans it exists to show.
    """
    try:
        with open(log_path(app), encoding="utf-8", errors="replace") as handle:
            lines = handle.read().splitlines()
    except OSError:
        return []
    return [line for line in lines
            if line.strip() and "[DEBUG]" not in line][-limit:]


def log_html(app):
    entries = recent_log(app)
    if not entries:
        return ("<p class='muted'>Nothing recorded yet. Lines appear as soon "
                "as a card is scanned.</p>")
    rendered = []
    for line in entries:
        bad = "[ERROR]" in line or "[CRITICAL]" in line
        warn = "[WARNING]" in line
        css = "logbad" if bad else ("logwarn" if warn else "")
        rendered.append("<span class='%s'>%s</span>" % (css, html.escape(line)))
    return "<pre>" + "\n".join(rendered) + "</pre>"


def spotifyd_html():
    lines = spotifyd_error_lines()
    if not lines:
        return ("<p class='muted'>No complaints since boot. Cleared whenever "
                "the Pi restarts.</p>")
    return "<pre>" + "\n".join(
        "<span class='logbad'>%s</span>" % html.escape(line)
        for line in lines) + "</pre>"


# --------------------------------------------------------------------------
# The page
# --------------------------------------------------------------------------

CSS = """
* { box-sizing: border-box; }
body { font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
       margin: 0; padding: 1.5rem 1.2rem 4rem; line-height: 1.55;
       color: #1c1c1e; background: #f6f6f8; }
main { max-width: 34rem; margin: 0 auto; }
h1 { font-size: 1.5rem; margin: 0 0 .3rem; }
h2 { font-size: 1.05rem; margin: 1.8rem 0 .5rem; }
.card { background: #fff; border-radius: 14px; padding: .4rem 1.2rem;
        box-shadow: 0 1px 3px rgba(0,0,0,.09); }
.row { display: flex; align-items: baseline; gap: .55rem;
       padding: .55rem 0; border-bottom: 1px solid #eee; }
.row:last-child { border-bottom: 0; }
.dot { flex: 0 0 auto; width: .7rem; height: .7rem; border-radius: 50%;
       background: #c9c9cf; }
.dot.ok { background: #23923d; } .dot.warn { background: #cc8b00; }
.dot.bad { background: #c62b2b; } .dot.flat { background: #c9c9cf; }
.label { flex: 1 1 45%; }
.value { flex: 1 1 55%; text-align: right; font-weight: 600;
         word-break: break-word; }
.muted { color: #666; font-size: .92rem; margin: .5rem 0; }
.text { padding: .8rem 1.2rem; }
pre { margin: 0; padding: .2rem 0; font-size: .78rem; line-height: 1.5;
      overflow-x: auto; white-space: pre; color: #444; }
pre .logbad { color: #c62b2b; font-weight: 700; }
pre .logwarn { color: #a06a00; }
a.button { display: block; width: 100%; text-align: center; margin-top: 1.5rem;
        background: #e6e6ea; color: #1c1c1e; border-radius: 10px;
        padding: .9rem 1rem; font-size: 1.05rem; font-weight: 600;
        text-decoration: none; }
@media (prefers-color-scheme: dark) {
  body { color: #ececf1; background: #121214; }
  .card { background: #1d1d20; box-shadow: none; }
  .row { border-bottom-color: #2b2b30; }
  .muted, pre { color: #a0a0aa; }
  a.button { background: #2b2b30; color: #ececf1; }
  pre .logbad { color: #ff6b6b; } pre .logwarn { color: #e0a63c; }
}
"""


def page(title, body):
    return (
        "<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<title>" + html.escape(title) + "</title><style>" + CSS + "</style>"
        "</head><body><main>" + body + "</main></body></html>"
    ).encode("utf-8")


def collect_status(app):
    """Every section of the page, in the order it is read"""
    snap = spotify_snapshot()
    return [
        ("Spotify", spotify_rows(snap)),
        ("Playback", playback_rows(app, snap)),
        ("Player", player_rows(app)),
        ("Device", device_rows()),
    ]


def status_page(app):
    blocks = []
    for title, rows in collect_status(app):
        items = "".join(
            "<div class='row'><span class='dot %s'></span>"
            "<span class='label'>%s</span>"
            "<span class='value'>%s</span></div>"
            % (state, html.escape(label), html.escape(str(value)))
            for label, value, state in rows)
        blocks.append("<h2>%s</h2><div class='card'>%s</div>"
                      % (html.escape(title), items))

    name = os.getenv("APP_NAME", "player")
    body = (
        "<h1>%s</h1>" % html.escape(name)
        + "<p class='muted'>%s</p>" % datetime.datetime.now().strftime(
            "%d %b %Y, %H:%M:%S")
        + "".join(blocks)
        + "<h2>Recent activity</h2><div class='card text'>%s</div>" % log_html(app)
        + "<h2>spotifyd complaints</h2><div class='card text'>%s</div>" % spotifyd_html()
        + "<a class='button' href='/'>Refresh</a>"
    )
    return page("%s status" % name, body)


# --------------------------------------------------------------------------
# Server
# --------------------------------------------------------------------------

def make_handler(app):
    class StatusHandler(BaseHTTPRequestHandler):
        server_version = "ToemStatus"

        def log_message(self, fmt, *args):
            # Into our own log at DEBUG, or every page view would drown the
            # card scans the log exists for.
            logging.debug("status page: " + fmt, *args)

        def respond(self, body, status=200):
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = urllib.parse.urlparse(self.path).path
            if path in ("/", "/status"):
                if app is not None:
                    # Someone is standing at the device looking at it, so it
                    # must not switch itself off mid-diagnosis. The page has
                    # no auto-refresh precisely so that a forgotten open tab
                    # cannot keep a battery device awake indefinitely.
                    app.reset_last_activity()
                try:
                    self.respond(status_page(app))
                except Exception as error:
                    # A status page that 500s tells whoever opened it nothing.
                    logging.exception("Status page failed: %s", error)
                    self.respond(page("Status", "<h1>Status unavailable</h1>"
                                      "<div class='card text'><p>%s</p></div>"
                                      % html.escape(str(error))), 500)
            else:
                self.respond(page("Not found", "<h1>Not found</h1>"
                                  "<a class='button' href='/'>Status</a>"), 404)

    return StatusHandler


def start(app, port=None):
    """Serve the status page on a daemon thread; returns the server

    Never lets a page take the player down: the caller treats a failure to
    bind as a missing page, not a failed startup.
    """
    if port is None:
        port = int(os.environ.get("STATUS_PORT", DEFAULT_PORT))
        if not port:
            logging.info("Status page disabled (STATUS_PORT=0)")
            return None
    # Only the environment can switch the page off that way. An explicit
    # port=0 is a request to serve on whatever port is free, which is how the
    # tests bind without fighting over 8080.

    server = ThreadingHTTPServer(("0.0.0.0", port), make_handler(app))
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True,
                     name="status-web").start()
    logging.info("Status page on http://%s:%d/", socket.gethostname(), port)
    return server


def main():
    """Serve the page without a player, for a device whose player is down"""
    from dotenv import load_dotenv
    load_dotenv()
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")
    if start(None):
        # start() already serves on its own thread; nothing left to do here
        # but stay alive.
        threading.Event().wait()


if __name__ == "__main__":
    main()
