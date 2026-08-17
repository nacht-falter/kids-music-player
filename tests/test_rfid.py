import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import pytest
from unittest.mock import MagicMock, patch

from rfid import RfidReader


def _device(name):
    dev = MagicMock()
    dev.name = name
    dev.path = "/dev/input/event0"
    return dev


def test_requires_device_name_env(monkeypatch):
    monkeypatch.delenv("RFID_READER", raising=False)
    with pytest.raises(ValueError, match="RFID_READER"):
        RfidReader()


def test_finds_device_immediately(monkeypatch):
    monkeypatch.setenv("RFID_READER", "SYC ID&IC")
    dev = _device("Sycreader RFID Technology Co., Ltd SYC ID&IC USB Reader")

    with patch("rfid.list_devices", return_value=["/dev/input/event0"]), \
            patch("rfid.InputDevice", return_value=dev):
        reader = RfidReader()

    assert reader.device is dev


def test_waits_for_a_device_that_appears_late(monkeypatch):
    """A reader still enumerating at boot must not kill the service"""
    monkeypatch.setenv("RFID_READER", "SYC ID&IC")
    dev = _device("Sycreader SYC ID&IC USB Reader")

    # Absent for the first two sweeps, then present.
    sweeps = [[], [], ["/dev/input/event0"]]

    with patch("rfid.list_devices", side_effect=sweeps), \
            patch("rfid.InputDevice", return_value=dev), \
            patch("rfid.time.sleep") as mock_sleep:
        reader = RfidReader()

    assert reader.device is dev
    assert mock_sleep.call_count == 2


def test_gives_up_after_retries(monkeypatch):
    monkeypatch.setenv("RFID_READER", "SYC ID&IC")

    with patch("rfid.list_devices", return_value=[]), \
            patch("rfid.time.sleep") as mock_sleep:
        with pytest.raises(FileNotFoundError, match="SYC ID&IC"):
            RfidReader()

    # Retries FIND_RETRIES times, sleeping between but not after the last.
    assert mock_sleep.call_count == RfidReader.FIND_RETRIES - 1


def test_non_matching_devices_are_closed(monkeypatch):
    """Otherwise every retry sweep leaks a file descriptor per device"""
    monkeypatch.setenv("RFID_READER", "SYC ID&IC")
    other = _device("Some Keyboard")
    wanted = _device("SYC ID&IC USB Reader")

    with patch("rfid.list_devices", return_value=["/dev/input/event0",
                                                  "/dev/input/event1"]), \
            patch("rfid.InputDevice", side_effect=[other, wanted]):
        reader = RfidReader()

    assert reader.device is wanted
    other.close.assert_called_once()
    wanted.close.assert_not_called()
