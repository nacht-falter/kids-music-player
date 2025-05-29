import logging
import os
import string

from evdev import InputDevice, categorize, ecodes, list_devices


class RfidReader:
    KEY_MAP = {f'KEY_{char}': char for char in string.digits +
               string.ascii_uppercase}

    def __init__(self, device_name_env="RFID_READER"):
        self.device_name = os.getenv(device_name_env)
        if not self.device_name:
            raise ValueError(f"Environment variable {device_name_env} not set")

        self.device = self._find_device(self.device_name)
        logging.info(
            f"Using RFID device: {self.device.path} ({self.device.name})")

    def _find_device(self, device_name):
        """Find device with name containing given string"""
        for path in list_devices():
            dev = InputDevice(path)
            if device_name in dev.name:
                return dev
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
