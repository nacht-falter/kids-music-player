import RPi.GPIO as GPIO
import os

GPIO.setup(3, GPIO.IN, pull_up_down=GPIO.PUD_UP)


def wait_for_power_button():
    GPIO.wait_for_edge(3, GPIO.FALLING)
    os.system("systemctl poweroff")
