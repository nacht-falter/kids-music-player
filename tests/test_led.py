import importlib
import os
import sys
import threading
import types

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import pytest


class FakeGPIO(types.ModuleType):
    """Just enough of RPi.GPIO to record what led.py does to a pin"""
    BCM = OUT = "unused"
    HIGH, LOW = True, False

    def __init__(self):
        super().__init__("RPi.GPIO")
        self.pins = {}
        self.writes = []

    def setmode(self, mode):
        pass

    def setwarnings(self, flag):
        pass

    def setup(self, pin, mode):
        self.pins[pin] = False

    def input(self, pin):
        return self.pins[pin]

    def output(self, pin, value):
        self.pins[pin] = bool(value)
        self.writes.append((pin, bool(value)))


@pytest.fixture
def led(monkeypatch):
    """led.py with RPi.GPIO swapped out, so it imports off a Pi"""
    gpio = FakeGPIO()
    rpi = types.ModuleType("RPi")
    rpi.GPIO = gpio
    monkeypatch.setitem(sys.modules, "RPi", rpi)
    monkeypatch.setitem(sys.modules, "RPi.GPIO", gpio)
    monkeypatch.delitem(sys.modules, "led", raising=False)
    module = importlib.import_module("led")
    module.gpio = gpio
    yield module
    monkeypatch.delitem(sys.modules, "led", raising=False)


def wait_for_blinks(led):
    for thread in threading.enumerate():
        if thread is not threading.current_thread() and thread.daemon:
            thread.join(timeout=1)
    assert not led._blink_lock.locked()


def test_a_lit_led_blinks_dark_and_comes_back(led):
    led.turn_on_led(23)
    led.gpio.writes.clear()

    led.blink(23, duration=0.01)
    wait_for_blinks(led)

    assert led.gpio.writes == [(23, False), (23, True)]


def test_a_dark_led_blinks_lit_and_goes_dark_again(led):
    led.turn_off_led(23)
    led.gpio.writes.clear()

    led.blink(23, duration=0.01)
    wait_for_blinks(led)

    assert led.gpio.writes == [(23, True), (23, False)]


def test_blink_does_not_block_the_caller(led):
    led.turn_on_led(23)
    led.blink(23, duration=0.5)
    # Still dark: the caller got control back mid-blink.
    assert led.gpio.pins[23] is False
    wait_for_blinks(led)
    assert led.gpio.pins[23] is True


def test_a_blink_in_progress_swallows_the_next(led):
    led.turn_on_led(23)
    led.gpio.writes.clear()

    led.blink(23, duration=0.2)
    led.blink(23, duration=0.2)
    wait_for_blinks(led)

    assert led.gpio.writes == [(23, False), (23, True)]
