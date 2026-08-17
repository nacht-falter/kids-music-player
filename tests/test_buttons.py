import os
import sys
import threading

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import pytest
from unittest.mock import MagicMock, patch


@pytest.fixture
def handler():
    """A ButtonHandler with gpiozero stubbed out"""
    import buttons
    with patch.object(buttons, "Button", MagicMock()):
        h = buttons.ButtonHandler(
            get_player=MagicMock(return_value=None),
            set_player=MagicMock(),
            database_url=":memory:",
            player_lock=threading.Lock(),
            reset_last_activity=MagicMock(),
        )
    h.SHUTDOWN_CONFIRM_TIMEOUT = 0.05  # keep the tests quick
    yield h
    h._clear_confirmation()


def test_single_press_only_confirms(handler):
    with patch("buttons.utils") as mock_utils:
        handler.handle_buttons("shutdown")
        mock_utils.play_sound.assert_called_once_with("confirm_shutdown")
        mock_utils.shutdown.assert_not_called()


def test_second_press_shuts_down(handler):
    with patch("buttons.utils") as mock_utils:
        handler.handle_buttons("shutdown")
        handler.handle_buttons("shutdown")
        mock_utils.shutdown.assert_called_once()


def test_confirmation_expires(handler):
    """An accidental press must not leave the device armed indefinitely"""
    with patch("buttons.utils") as mock_utils:
        handler.handle_buttons("shutdown")

        # Wait out the confirmation window.
        expired = threading.Event()
        expired.wait(handler.SHUTDOWN_CONFIRM_TIMEOUT + 0.15)

        handler.handle_buttons("shutdown")
        # The second press re-confirms rather than shutting down.
        mock_utils.shutdown.assert_not_called()
        assert mock_utils.play_sound.call_count == 2


def test_third_rapid_press_still_shuts_down(handler):
    """`== 2` left a rapid triple-press matching neither branch"""
    with patch("buttons.utils") as mock_utils:
        handler.handle_buttons("shutdown")
        handler.handle_buttons("shutdown")
        mock_utils.shutdown.reset_mock()

        # Counter was cleared, so this starts a fresh confirmation.
        handler.handle_buttons("shutdown")
        mock_utils.shutdown.assert_not_called()
        handler.handle_buttons("shutdown")
        mock_utils.shutdown.assert_called_once()


def test_other_button_cancels_the_arming(handler):
    with patch("buttons.utils") as mock_utils:
        handler.handle_buttons("shutdown")
        handler.handle_buttons("next_track")
        handler.handle_buttons("shutdown")
        mock_utils.shutdown.assert_not_called()
