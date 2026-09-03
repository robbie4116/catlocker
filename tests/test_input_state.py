from hotkeys import (
    InputState,
    KeyEvent,
    Transition,
    VK_LCONTROL,
    VK_RCONTROL,
    parse_shortcut,
)

F24 = 0x87
K = 0x4B
LSHIFT = 0xA0


def down(vk, *, injected=False):
    return KeyEvent(vk=vk, is_keydown=True, injected=injected)


def up(vk, *, injected=False):
    return KeyEvent(vk=vk, is_keydown=False, injected=injected)


def test_f24_is_a_true_toggle_and_never_leaks():
    state = InputState(parse_shortcut("F24"))
    first = state.handle(down(F24))
    assert first == Transition(suppress=True, locked=True, changed=True, reason="toggle")
    assert state.handle(up(F24)).suppress is True
    second = state.handle(down(F24))
    assert second == Transition(suppress=True, locked=False, changed=True, reason="toggle")


def test_held_toggle_activates_only_once_until_release():
    state = InputState(parse_shortcut("F24"))
    assert state.handle(down(F24)).changed is True
    repeat = state.handle(down(F24))
    assert repeat.changed is False
    assert repeat.locked is True
    state.handle(up(F24))
    assert state.handle(down(F24)).changed is True


def test_exact_dual_control_always_unlocks_and_never_locks():
    state = InputState(parse_shortcut("F24"), locked=True)
    state.handle(down(VK_LCONTROL))
    result = state.handle(down(VK_RCONTROL))
    assert result == Transition(suppress=True, locked=False, changed=True, reason="emergency")
    state.handle(up(VK_RCONTROL))
    state.handle(up(VK_LCONTROL))
    state.set_locked(False)
    state.handle(down(VK_RCONTROL))
    result = state.handle(down(VK_LCONTROL))
    assert result.locked is False
    assert result.reason == "emergency"


def test_one_control_key_is_not_the_emergency_chord():
    state = InputState(parse_shortcut("F24"), locked=True)
    result = state.handle(down(VK_LCONTROL))
    assert result.changed is False
    assert result.suppress is True


def test_emergency_fires_once_until_either_control_is_released():
    state = InputState(parse_shortcut("F24"), locked=True)
    state.handle(down(VK_LCONTROL))
    assert state.handle(down(VK_RCONTROL)).reason == "emergency"
    state.set_locked(True)
    repeat = state.handle(down(VK_RCONTROL))
    assert repeat.changed is False
    assert repeat.locked is True
    state.handle(up(VK_RCONTROL))
    assert state.handle(down(VK_RCONTROL)).reason == "emergency"


def test_injected_f24_uses_the_normal_toggle_path():
    state = InputState(parse_shortcut("F24"))
    assert state.handle(down(F24, injected=True)).reason == "toggle"
