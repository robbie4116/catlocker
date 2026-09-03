import ctypes
import pytest

from keyboard_hook import (
    HC_ACTION,
    HOOKPROC,
    KBDLLHOOKSTRUCT,
    LLKHF_INJECTED,
    WM_KEYDOWN,
    WM_KEYUP,
    WM_SYSKEYDOWN,
    WM_SYSKEYUP,
    event_from_message,
)


@pytest.mark.parametrize(
    ("message", "is_keydown"),
    [
        (WM_KEYDOWN, True),
        (WM_SYSKEYDOWN, True),
        (WM_KEYUP, False),
        (WM_SYSKEYUP, False),
    ],
)
def test_keyboard_messages_translate_to_pure_events(message, is_keydown):
    data = KBDLLHOOKSTRUCT(vkCode=0x87, scanCode=0, flags=LLKHF_INJECTED, time=0, dwExtraInfo=0)
    event = event_from_message(message, data)
    assert event.vk == 0x87
    assert event.is_keydown is is_keydown
    assert event.injected is True


def test_unknown_message_is_not_a_keyboard_event():
    assert event_from_message(0x9999, KBDLLHOOKSTRUCT()) is None


def test_kbd_structure_uses_pointer_sized_extra_info():
    field_type = dict(KBDLLHOOKSTRUCT._fields_)["dwExtraInfo"]
    assert ctypes.sizeof(field_type) == ctypes.sizeof(ctypes.c_void_p)


def test_hook_callback_uses_pointer_sized_windows_types():
    assert ctypes.sizeof(HOOKPROC._argtypes_[1]) == ctypes.sizeof(ctypes.c_void_p)
    assert ctypes.sizeof(HOOKPROC._argtypes_[2]) == ctypes.sizeof(ctypes.c_void_p)
    assert ctypes.sizeof(HOOKPROC._restype_) == ctypes.sizeof(ctypes.c_void_p)
