import logging
import os
import string
import time

from evdev import InputDevice, categorize, ecodes, list_devices


class RfidReader:
    KEY_MAP = {f'KEY_{char}': char for char in string.digits +
               string.ascii_uppercase}

    # USB enumeration can lag service start at boot. Without retrying, a reader
    # that is merely slow to appear raises FileNotFoundError, which exits the
    # app; systemd then burns StartLimitBurst=5 restarts in well under its 10s
    # window and parks the unit in a failed state until someone intervenes.
    FIND_RETRIES = 15
    FIND_DELAY = 2

    def __init__(self, device_name_env="RFID_READER"):
        self.device_name = os.getenv(device_name_env)
        if not self.device_name:
            raise ValueError(f"Environment variable {device_name_env} not set")

        self.device = self._find_device(self.device_name)
        logging.info(
            f"Using RFID device: {self.device.path} ({self.device.name})")

    def _find_device(self, device_name):
        """Find device with name containing given string, waiting for it"""
        for attempt in range(self.FIND_RETRIES):
            for path in list_devices():
                dev = InputDevice(path)
                if device_name in dev.name:
                    return dev
                dev.close()  # otherwise each retry leaks a descriptor

            if attempt < self.FIND_RETRIES - 1:
                logging.warning(
                    "No input device matching %r (attempt %d/%d), retrying in %ds",
                    device_name, attempt + 1, self.FIND_RETRIES, self.FIND_DELAY)
                time.sleep(self.FIND_DELAY)

        raise FileNotFoundError(
            f"No input device found matching: {device_name}")

    def read_code(self):
        code = ""
        for event in self.device.read_loop():
            if event.type == ecodes.EV_KEY:
                key_event = categorize(event)
                if key_event.keystate == key_event.key_down:
                    key_name = key_event.keycode
                    if key_name == "KEY_ENTER":
                        return code
                    elif key_name in self.KEY_MAP:
                        code += self.KEY_MAP[key_name]

    def close(self):
        self.device.close()
