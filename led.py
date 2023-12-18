import RPi.GPIO as GPIO
import time

GPIO.setmode(GPIO.BCM)
GPIO.setwarnings(False)
GPIO.setup(23, GPIO.OUT)


def toggle_led():
    GPIO.output(23, not GPIO.input(23))


def flash_led():
    for i in range(0, 2):
        toggle_led()
        time.sleep(0.5)
        toggle_led()
        time.sleep(0.5)
