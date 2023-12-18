import RPi.GPIO as GPIO
import time

GPIO.setmode(GPIO.BCM)
GPIO.setwarnings(False)
GPIO.setup(14, GPIO.OUT)
GPIO.setup(23, GPIO.OUT)


def toggle_led(gpio_pin):
    GPIO.output(gpio_pin, not GPIO.input(gpio_pin))


def flash_led(gpio_pin):
    for i in range(0, 2):
        toggle_led(gpio_pin)
        time.sleep(0.5)
        toggle_led(gpio_pin)
        time.sleep(0.5)
