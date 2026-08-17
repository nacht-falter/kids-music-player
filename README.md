# Kids Music Player v2.0

A Raspberry Pi-based music player that uses RFID cards to control playback. Supports both local music files via MPD and Spotify streaming.

## Installation

### 1. Install Python Dependencies

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Install and Configure MPD/MPC

```bash
sudo apt update
sudo apt install mpd mpc
```

#### Setup MPD

**Option A: System-wide MPD (Simpler)**

Edit `/etc/mpd.conf`:
```conf
music_directory    "/path/to/music/library"
audio_output {
    type        "alsa"
    name        "My ALSA Device"
    # device    "hw:0,0"  # Uncomment and adjust if needed
}
```

Start and test:
```bash
sudo systemctl enable mpd.socket
mpc update
mpc listall
```

**Option B: User MPD (Recommended)**

Create `~/.config/mpd/mpd.conf`:
```conf
music_directory    "~/path/to/music/library"
playlist_directory "~/.config/mpd/playlists"
db_file            "~/.config/mpd/database"
log_file           "~/.config/mpd/log"
pid_file           "~/.config/mpd/pid"
state_file         "~/.config/mpd/state"

audio_output {
    type        "alsa"
    name        "My ALSA Device"
    # device    "hw:0,0"  # Uncomment and adjust if needed
}
```

Start and test:
```bash
systemctl --user enable mpd
systemctl --user start mpd
mpc update
mpc listall
```
See [MPD documentation](https://mpd.readthedocs.io/en/stable/index.html) for advanced configuration.

### 3. Install and Configure Spotifyd

Install `jq`:

```bash
sudo apt-get install jq
```

Follow the [official Spotifyd installation guide](https://docs.spotifyd.rs/installation/index.html) for your system.

See the [Spotifyd configuration docs](https://docs.spotifyd.rs/configuration/index.html) for full options and [authentication setup](https://docs.spotifyd.rs/configuration/auth.html).

Create configuration at `~/.config/spotifyd/spotifyd.conf`:

```conf
[global]
username_cmd = "jq -r .username /path/to/credentials.json"
backend = "alsa"
mixer = "PCM"
device_name = "device_name"
bitrate = 160
cache_path = "/path/to/cache/spotifyd"
max_cache_size = 1000000000
no_audio_cache = true
initial_volume = "100"
volume_normalisation = true
normalisation_pregain = 10
device_type = "speaker"
```

Test your configuration:

```bash
spotifyd --no-daemon --verbose
```

Set up as a [systemd service](https://docs.spotifyd.rs/advanced/systemd.html) (recommended):

Create `~/.config/systemd/user/spotifyd-service`:

```ini
[Unit]
Description=A spotify playing daemon
Documentation=https://github.com/Spotifyd/spotifyd
Wants=sound.target
After=sound.target
Wants=network-online.target
After=network-online.target

[Service]
ExecStart=/path/to/spotifyd --no-daemon
Restart=always
RestartSec=12

[Install]
WantedBy=default.target
```

```bash
sudo systemctl --user enable spotifyd
sudo systemctl --user start spotifyd
```

### 4. Get Spotify API Credentials

#### Step 1: Create a Spotify App
1. Go to [Spotify Developer Dashboard](https://developer.spotify.com/dashboard)
2. Create a new app and note your `client_id` and `client_secret`
3. Set the Redirect URI to: `https://johannesbernet.com/spotify/callback`

#### Step 2: Get Your Refresh Token
You can use this online helper tool to get a refresh token: [https://johannesbernet.com/spotify/auth](https://johannesbernet.com/spotify/auth)

*Note: This tool is completely client-side - your credentials are not stored on the server.*

**Process:**
1. Go to [https://johannesbernet.com/spotify/auth](https://johannesbernet.com/spotify/auth)
2. Fill in the form with:
   - Your **Client ID**
   - Your **Client Secret** 
   - Select the scopes your app needs:
     - `user-read-playback-state`
     - `user-modify-playback-state`
     - `user-read-currently-playing`
     - `user-read-playback-position`
     - `playlist-read-private` (optional)
3. Click **"Start Authorization"**
4. Log in to Spotify and approve access
5. You'll be redirected to the callback page with a pre-filled `curl` command

#### Step 3: Execute the Token Request
On the callback page, you'll see a command like this:

```bash
curl -X POST https://accounts.spotify.com/api/token \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "grant_type=authorization_code" \
  -d "code=..." \
  -d "redirect_uri=https://johannesbernet.com/spotify/callback" \
  -u "YOUR_CLIENT_ID:YOUR_CLIENT_SECRET"
```

**Copy and run this command in your terminal.**

#### Step 4: Extract Your Refresh Token
The response will look like this:

```json
{
  "access_token": "...",
  "token_type": "Bearer",
  "expires_in": 3600,
  "refresh_token": "..."
}
```

**Optional:** If you have `jq` installed, extract just the refresh token:

```bash
curl -s -X POST https://accounts.spotify.com/api/token \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "grant_type=authorization_code" \
  -d "code=..." \
  -d "redirect_uri=https://johannesbernet.com/spotify/callback" \
  -u "YOUR_CLIENT_ID:YOUR_CLIENT_SECRET" | jq -r '.refresh_token'
```

Remember your `client_id`, `client_secret`, and `refresh_token` for your application configuration.

#### Re-authorizing later (required every 6 months)

Spotify expires refresh tokens six months after authorization, so the player will stop working with
`400 invalid_grant` until it is re-authorized. `reauth.py` automates the flow above:

```bash
python reauth.py --host toem2      # update .env on a device over ssh
python reauth.py                   # or operate on a local .env
python reauth.py --dry-run         # check without writing
```

It prints an authorization URL, takes the pasted code (or the whole redirect URL), and then
**validates before installing**: the new token must belong to the same Spotify account `spotifyd` is
logged in as, and the configured `SPOTIFY_DEVICE_ID` must appear in that account's device list. This
matters because a token issued to the wrong account authenticates perfectly and fails only later,
with an empty device list and cards that silently do nothing.

It backs up `.env`, records `SPOTIFY_AUTH_DATE`, and restarts the service.

### 5. Get your Spotifyd device ID:

```bash
spotifyd --no-daemon --verbose 2>&1 | awk "/Using device id/ { gsub(\"'\", \"\", \$NF); print \$NF; exit }"
```

### 6. Find RFID Reader Device

Plug in your RFID reader and run:

```bash
python -m evdev.evtest
```

Find the device name of your RFID reader.

### 7. Configure Environment Variables

#### Option A: Setup Script (Recommended)
There is a setup script available which helps you validate your setup and configure the required environment variables. You will need to have MPD/MPC and Spotifyd setup correctly and have your Spotify API credentials at hand.

```bash
python setup_env.py
```

#### Option B: Manual Configuration
Alternatively, create a `.env` file manually in the project root:

```env
SPOTIFY_USERCREDS=base64_clientid_clientsecret
SPOTIFY_REFRESH_TOKEN=your_refresh_token
SPOTIFY_DEVICE_ID=kids-music-player
DATABASE_URL=/path/to/db
RFID_READER=rfid_device_name

# Written by reauth.py; used to warn before the 6-month expiry.
# Absent means "unknown", never "expired".
# SPOTIFY_AUTH_DATE=2026-08-16

# Optional settings:
# APP_NAME=your_app_name
# IDLE_TIME=3600

# DEVELOPMENT changes two unrelated things, so it is worth being deliberate:
#   - logging drops from DEBUG to INFO when false
#   - the idle watchdog and the shutdown button run `sudo shutdown -h now`
#     when false, but merely exit the process when true
# With `Restart=on-failure`, that clean exit is not a failure, so the service
# stays stopped while the Pi keeps running - the device looks powered on but
# ignores every card. Leave it false on a real device.
# DEVELOPMENT=true
```

### 8. Run the Player

```bash
source venv/bin/activate
python main.py
```

### 9. Optional: Run as System Service

Create `/etc/systemd/system/kids-music-player.service`:

```ini
[Unit]
Description=Kids Music Player
After=sound.target
Wants=sound.target

[Service]
ExecStart=/path/to/app/venv/bin/python main.py
WorkingDirectory=/path/to/app
StandardOutput=journal
StandardError=journal
Restart=on-failure
User=pi

[Install]
WantedBy=default.target
```

Note the shutdown button and the idle watchdog run `sudo shutdown -h now`. Under
`User=pi` that needs passwordless sudo for the shutdown command; running the unit as root
avoids it.

Enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable kids-music-player
sudo systemctl start kids-music-player

# Check status
sudo systemctl status kids-music-player
```

### 10. Optional: Remote Database Sync

To sync RFID card mappings across multiple devices, you can set up remote database synchronization:

1. Host your own sync API using [toem-api](https://github.com/nacht-falter/toem-api)
2. Add sync configuration to your `.env` file:

```env
ENABLE_SYNC=true
SYNC_API_URL=https://your.api.url
SYNC_API_TOKEN=your_api_token
```

This allows multiple music players to share the same RFID card database, so cards work consistently across all your devices. You can also register new RFID codes remotely without turning on the device.

## Hardware Setup

- Connect RFID reader via USB
- Connect speakers/headphones to Pi's audio output
- Optional: Wire GPIO buttons/LEDs as per your hardware configuration

## Troubleshooting

### MPD Issues
```bash
# Check MPD logs
sudo journalctl -u mpd -f
```

### Spotifyd Issues
```bash
# Check Spotifyd logs
journalctl --user -u spotifyd -f

# Test connection manually
spotifyd --no-daemon --verbose
```

### RFID Reader Issues
```bash
# Check if device is detected
lsusb
dmesg | grep -i usb

# Test input events
sudo cat /dev/input/event0  # Replace with your device
```

### Audio Issues
```bash
# List audio devices
aplay -l

# Test audio output
speaker-test -c2
```

### Service Issues
```bash
# Check service logs
sudo journalctl -u kids-music-player -f
```

## Adding Music

### Local Files
Place music in MPD music folder and run `mpc update`

### Spotify Content

Both registration tools search Spotify directly, so URIs rarely need copying. If you do
want one — Spotify app → Share → Copy Spotify URI — it looks like
`spotify:album:1chTWZhEdZvGu8sMVuZt2W`, and both tools also accept the
`https://open.spotify.com/album/...` link that "Copy link" gives you.

### Register RFID Cards

**From a browser (including a phone):** <https://johannesbernet.com/toem/register>

Sign in, type or scan the card number, search for an album and save. Nothing is copied by
hand and the player does not need to be switched on — it picks new cards up within a
minute of being on.

**From the terminal:**
```bash
python3 register_rfid.py
```

Same idea: `(a)dd`, `(d)elete`, `(l)ist`. Search by album name and the URI and title fill
themselves in; pasted `open.spotify.com` links work too.

Card numbers are stored zero-padded to ten digits, but both tools accept the number as
printed on the card and pad it, so `1221753` finds `0001221753`.

The RFID reader is a USB HID device: it types the digits and sends Enter, so scanning a
card straight into either prompt fills it in and moves on.
