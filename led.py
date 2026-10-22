import threading
import time

import RPi.GPIO as GPIO

GPIO.setmode(GPIO.BCM)
GPIO.setwarnings(False)
GPIO.setup(14, GPIO.OUT)
GPIO.setup(23, GPIO.OUT)


def toggle_led(gpio_pin):
    GPIO.output(gpio_pin, not GPIO.input(gpio_pin))


def turn_on_led(gpio_pin):
    GPIO.output(gpio_pin, GPIO.HIGH)


def turn_off_led(gpio_pin):
    GPIO.output(gpio_pin, GPIO.LOW)


def flash_led(gpio_pin):
    for _ in range(2):
        toggle_led(gpio_pin)
        time.sleep(0.1)
        toggle_led(gpio_pin)
        time.sleep(0.1)


_blink_lock = threading.Lock()


def blink(gpio_pin, duration=0.1):
    """Invert the LED once, briefly, without blocking the caller

    Toggles rather than forcing off-then-on, so a steady LED blinks dark and a
    dark one blinks lit, and a blink overlapping start_flashing() leaves the
    pin where that loop expects it. A blink already in progress swallows the
    next one: held volume keys repeat faster than a blink lasts.
    """
    if not _blink_lock.acquire(blocking=False):
        return

    def _blink():
        try:
            toggle_led(gpio_pin)
            time.sleep(duration)
            toggle_led(gpio_pin)
        finally:
            _blink_lock.release()

    threading.Thread(target=_blink, daemon=True).start()


def start_flashing(gpio_pin, interval=0.3):
    stop_event = threading.Event()

    def _flash_loop():
        while not stop_event.is_set():
            flash_led(gpio_pin)
            time.sleep(interval)

    thread = threading.Thread(target=_flash_loop, daemon=True)
    thread.start()
    return stop_event, thread


def stop_flashing(stop_event, thread):
    stop_event.set()
    thread.join()
