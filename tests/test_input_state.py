import random

import pytest

from hotkeys import (
    InputState,
    KeyEvent,
    Transition,
    VK_CONTROL,
    VK_LCONTROL,
    VK_RCONTROL,
    VK_LSHIFT,
    VK_RSHIFT,
    UnpairedKeyEvent,
    parse_shortcut,
)
from key_identity import normalize_key_event

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


def test_passed_modifier_releases_after_shortcut_locks():
    state = InputState(parse_shortcut("Ctrl+Shift+K"))
    assert state.handle(down(VK_LCONTROL)).suppress is False
    assert state.handle(down(LSHIFT)).suppress is False
    assert state.handle(down(K)).reason == "toggle"
    assert state.handle(up(K)).suppress is True
    assert state.handle(up(LSHIFT)).suppress is False
    assert state.handle(up(VK_LCONTROL)).suppress is False


def test_suppressed_chord_releases_after_shortcut_unlocks():
    state = InputState(parse_shortcut("Ctrl+Shift+K"), locked=True)
    assert state.handle(down(VK_LCONTROL)).suppress is True
    assert state.handle(down(LSHIFT)).suppress is True
    assert state.handle(down(K)).reason == "toggle"
    assert state.locked is False
    assert state.handle(up(K)).suppress is True
    assert state.handle(up(LSHIFT)).suppress is True
    assert state.handle(up(VK_LCONTROL)).suppress is True


def test_passed_key_repeat_is_blocked_after_lock_but_release_passes():
    state = InputState(parse_shortcut("F24"))
    assert state.handle(down(K)).suppress is False
    state.set_locked(True)
    assert state.handle(down(K)).suppress is True
    assert state.handle(up(K)).suppress is False


def test_suppressed_key_repeat_stays_blocked_after_unlock():
    state = InputState(parse_shortcut("F24"), locked=True)
    assert state.handle(down(K)).suppress is True
    state.set_locked(False)
    assert state.handle(down(K)).suppress is True
    assert state.handle(up(K)).suppress is True


def test_unrelated_keys_do_not_prevent_toggle_but_extra_modifiers_do():
    state = InputState(parse_shortcut("Ctrl+Shift+K"))
    state.handle(down(0x41))
    state.handle(down(VK_LCONTROL))
    state.handle(down(LSHIFT))
    assert state.handle(down(K)).reason == "toggle"

    state = InputState(parse_shortcut("Ctrl+Shift+K"))
    state.handle(down(VK_LCONTROL))
    state.handle(down(LSHIFT))
    state.handle(down(0xA4))
    assert state.handle(down(K)).reason is None


def test_recording_suspends_toggle_matching_but_not_emergency():
    state = InputState(parse_shortcut("F24"))
    assert state.enter_recording() is True
    result = state.handle(down(F24))
    assert result.reason is None
    assert result.suppress is False
    state.handle(up(F24))
    state.handle(down(VK_LCONTROL))
    assert state.handle(down(VK_RCONTROL)).reason == "emergency"
    state.handle(up(VK_RCONTROL))
    state.handle(up(VK_LCONTROL))
    state.set_locked(True)
    assert state.recording is False


def test_recording_cannot_start_locked():
    state = InputState(parse_shortcut("F24"), locked=True)
    assert state.enter_recording() is False


def test_new_trigger_already_held_requires_release_before_toggle():
    state = InputState(parse_shortcut("F24"))
    state.handle(down(K))
    assert state.replace_shortcut(parse_shortcut("K")) is True
    assert state.handle(down(K)).reason is None
    state.handle(up(K))
    assert state.handle(down(K)).reason == "toggle"


def test_releasing_old_trigger_after_replacement_does_not_toggle():
    state = InputState(parse_shortcut("F24"), locked=True)
    state.handle(down(F24))
    assert state.locked is False
    assert state.replace_shortcut(parse_shortcut("K")) is True
    assert state.handle(up(F24)).changed is False


def test_shortcut_replacement_is_rejected_while_locked():
    state = InputState(parse_shortcut("F24"), locked=True)
    assert state.replace_shortcut(parse_shortcut("K")) is False
    assert state.shortcut.canonical == "F24"


def test_replacement_exits_recording_mode():
    state = InputState(parse_shortcut("F24"))
    state.enter_recording()
    assert state.replace_shortcut(parse_shortcut("K")) is True
    assert state.recording is False


def test_untracked_late_keyup_follows_current_lock_state():
    assert InputState(parse_shortcut("F24")).handle(up(K)).suppress is False
    assert InputState(parse_shortcut("F24"), locked=True).handle(up(K)).suppress is True


def assert_disposition_partition(state):
    assert state.passed_down.isdisjoint(state.suppressed_down)
    assert state.passed_down | state.suppressed_down == state.pressed


def test_deterministic_cat_mashing_preserves_disposition_partition():
    state = InputState(parse_shortcut("F24"))
    media_vks = (0xAD, 0xAE, 0xAF, 0xB0, 0xB1, 0xB2, 0xB3)
    tracked_vks = (
        *range(0x41, 0x5B),
        VK_LCONTROL,
        VK_RCONTROL,
        VK_LSHIFT,
        VK_RSHIFT,
        F24,
        *media_vks,
    )
    rng = random.Random(20260903)

    for vk in tracked_vks:
        for event in (down(vk), down(vk), up(vk)):
            state.handle(event)
            assert_disposition_partition(state)

    for index in range(160):
        if index % 9 == 0:
            state.set_locked(bool(rng.getrandbits(1)))
            assert_disposition_partition(state)
        if state.pressed and rng.random() < 0.35:
            event = down(rng.choice(tuple(state.pressed)))
        else:
            vk = rng.choice(tracked_vks)
            event = down(vk) if rng.random() < 0.6 else up(vk)
        state.handle(event)
        assert_disposition_partition(state)

    for vk in tuple(sorted(state.pressed)):
        state.handle(up(vk))
        assert_disposition_partition(state)

    assert state.pressed == set()
    assert state.passed_down == set()
    assert state.suppressed_down == set()


@pytest.mark.parametrize(
    "modifier",
    [
        "LCtrl",
        "RCtrl",
        "LAlt",
        "RAlt",
        "LShift",
        "RShift",
        "LWin",
        "RWin",
    ],
)
@pytest.mark.parametrize("locked", [False, True])
def test_standalone_modifier_toggles_once_on_eligible_release(modifier, locked):
    shortcut = parse_shortcut(modifier)
    state = InputState(shortcut, locked=locked)
    down_result = state.handle(down(shortcut.trigger_vk))

    assert down_result.changed is False
    assert down_result.locked is locked

    up_result = state.handle(up(shortcut.trigger_vk))

    assert up_result.changed is True
    assert up_result.reason == "toggle"
    assert up_result.locked is not locked
    assert state.handle(up(shortcut.trigger_vk)).changed is False


def test_standalone_modifier_does_not_toggle_after_intervening_key():
    state = InputState(parse_shortcut("RAlt"))
    state.handle(down(0xA5))
    state.handle(down(0x41))
    state.handle(up(0x41))

    assert state.handle(up(0xA5)).changed is False


def test_locked_standalone_modifier_counts_blocked_keys_for_eligibility():
    state = InputState(parse_shortcut("LCtrl"), locked=True)
    state.handle(down(VK_LCONTROL))
    state.handle(down(0x41))
    state.handle(up(0x41))

    result = state.handle(up(VK_LCONTROL))

    assert result.changed is False
    assert state.locked is True


def test_emergency_unlock_invalidates_standalone_ctrl_tap():
    state = InputState(parse_shortcut("LCtrl"), locked=True)
    state.handle(down(VK_LCONTROL))

    assert state.handle(down(VK_RCONTROL)).reason == "emergency"
    state.handle(up(VK_RCONTROL))
    result = state.handle(up(VK_LCONTROL))

    assert result.changed is False
    assert state.locked is False


@pytest.mark.parametrize("operation", ["recording", "replacement", "command"])
def test_standalone_tap_is_invalidated_by_state_reset_operations(operation):
    state = InputState(parse_shortcut("RAlt"))
    state.handle(down(0xA5))

    if operation == "recording":
        assert state.enter_recording() is True
        state.exit_recording()
    elif operation == "replacement":
        assert state.replace_shortcut(parse_shortcut("RAlt")) is True
    else:
        state.set_locked(False)

    assert state.handle(up(0xA5)).changed is False


def test_passed_standalone_modifier_release_is_delivered_when_release_locks():
    state = InputState(parse_shortcut("RAlt"))

    assert state.handle(down(0xA5)).suppress is False
    result = state.handle(up(0xA5))

    assert result.suppress is False
    assert result.locked is True


def test_suppressed_standalone_modifier_release_is_delivered_when_release_unlocks():
    state = InputState(parse_shortcut("RAlt"), locked=True)

    assert state.handle(down(0xA5)).suppress is True
    result = state.handle(up(0xA5))

    assert result.suppress is True
    assert result.locked is False


def test_resolved_modifier_down_pairs_with_unresolved_modifier_release():
    state = InputState(parse_shortcut("Ctrl+K"))
    state.handle(
        normalize_key_event(
            VK_CONTROL,
            scan_code=0x1D,
            flags=0,
            is_keydown=True,
        )
    )

    state.handle(
        normalize_key_event(
            VK_CONTROL,
            scan_code=0,
            flags=0,
            is_keydown=False,
        )
    )

    assert state.pressed == set()


def test_unresolved_modifier_down_pairs_with_resolved_modifier_release():
    state = InputState(parse_shortcut("Ctrl+K"))
    state.handle(
        normalize_key_event(
            VK_CONTROL,
            scan_code=0,
            flags=0,
            is_keydown=True,
        )
    )

    state.handle(
        normalize_key_event(
            VK_CONTROL,
            scan_code=0x1D,
            flags=0,
            is_keydown=False,
        )
    )

    assert state.pressed == set()


def test_ambiguous_modifier_release_fails_safe_when_both_sides_are_held():
    state = InputState(parse_shortcut("Ctrl+K"))
    state.handle(down(VK_LCONTROL))
    state.handle(down(VK_RCONTROL))

    with pytest.raises(UnpairedKeyEvent):
        state.handle(
            normalize_key_event(
                VK_CONTROL,
                scan_code=0,
                flags=0,
                is_keydown=False,
            )
        )
