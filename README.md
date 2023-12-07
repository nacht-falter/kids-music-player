# Kids Music Player

This is the code for my contactless music player for kids, which I first built in 2016 with a Raspberry Pi and an RFID card reader. 

I am rewriting the code in Python at the moment. The new version will be able to play music from Spotify as well as from local files.

The original version of the player used [MPD](https://www.musicpd.org/) (Music Player Daemon) and a simple shell script for reading RFID cards and handling audio playback. Button presses are sent to the Raspberry Pi via infrared using [lirc](https://lirc.org).

The original version written in Bash can be found [here](https://github.com/nacht-falter/kids-music-player/tree/legacy)

![Music Player](musicplayer.JPG)
