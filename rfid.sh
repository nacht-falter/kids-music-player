#!/bin/bash

# Functions:
# Check playback status
pb_status () {
  if mpc status | grep -q playing; then
    playing=mpd
  else
    playing=false
  fi
}

# Ignore previous input 
hideinput(){
  [ -t 0 ] && stty -echo -icanon time 0 min 0
}

cleanup(){
  [ -t 0 ] && stty sane
}
trap cleanup EXIT
trap hideinput CONT

# Pause playback and play start sound
mpc pause
aplay /home/pi/toem/sounds/xyl_start.wav

while true; do
  while read -t 0.001 -n 10000 discard; do :; done # discards old input
  cleanup
  read -p "Waiting for RFID: "  -t 3600 rfid # wait for rfid input
  exit=$?

  hideinput

  if [ -n "$rfid" ]; then
    album=$(awk -F'|' -v rfid="$rfid" '$1 == rfid {print $2}' /home/pi/toem/albumlist) # retrieve album name from /home/pi/toem/albumlist
    pb_status # check playback status

    case $playing in
      false)
      # new album:
      if [ -n "$album" ] && [ "$rfid" != "$checkrfid" ]; then
        aplay /home/pi/toem/sounds/bell_short.wav
        mpc clear; mpc search album "$album" | mpc add; mpc play
        checkrfid="$rfid" # save current album name for reference
        # sleep 1

      # album already loaded:
      elif [ -n "$album" ] && [ "$rfid" = "$checkrfid" ]; then
        aplay /home/pi/toem/sounds/bell_short.wav
        mpc play
        checkrfid="$rfid" # save current album name for reference
        # sleep 1

        # unknown card (idle):
      else
        aplay /home/pi/toem/sounds/tabla_dun.wav
        # sleep 1
      fi
      ;;

      mpd) 
      # new album:
      if [ -n "$album" ] && [ "$rfid" != "$checkrfid" ]; then
        mpc pause
        aplay /home/pi/toem/sounds/bell_short.wav
        mpc clear; mpc search album "$album" | mpc add; mpc play
        checkrfid="$rfid"
        # sleep 1

        # album already loaded:
      elif [ "$rfid" = "$checkrfid" ]; then
        mpc pause
        aplay /home/pi/toem/sounds/bell_short.wav
        mpc play
        # sleep 1

        # unknown card (playing):
      else
        mpc pause
        aplay /home/pi/toem/sounds/tabla_dun.wav
        mpc play
        # sleep 1
      fi
      ;;
    esac
  fi
  pb_status
  # break loop and shutdown if still idle after an hour
  if [ "$exit" != 0 ] && [ "$playing" = false ]; then
    break
  fi
done
sudo /home/pi/toem/shutdown.sh
