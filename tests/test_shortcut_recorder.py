from hotkeys import (
    KeyEvent,
    Modifier,
    VK_CONTROL,
    VK_LCONTROL,
    VK_LSHIFT,
    VK_RMENU,
    VK_RSHIFT,
    parse_shortcut,
)
from shortcut_recorder import ShortcutRecorder


def event(vk, down=True, **kwargs):
    return KeyEvent(vk=vk, is_keydown=down, **kwargs)


def test_combination_preview_preserves_modifier_sides_and_completes_on_trigger_down():
    recorder = ShortcutRecorder("session-1")

    recorder.consume(event(VK_LCONTROL))
    assert recorder.held_preview == "LCtrl"
    recorder.consume(event(VK_LSHIFT))
    assert recorder.held_preview == "LCtrl+LShift"

    recorder.consume(event(0x4B))

    assert recorder.candidate == parse_shortcut("LCtrl+LShift+K")
    assert recorder.explanation is None
    assert recorder.terminal is True


def test_standalone_modifier_candidate_completes_on_eligible_release():
    recorder = ShortcutRecorder("session-2")

    recorder.consume(event(VK_RMENU))
    assert recorder.candidate is None
    recorder.consume(event(VK_RMENU, down=False))

    assert recorder.candidate == parse_shortcut("RAlt")
    assert recorder.terminal is True


def test_modifier_repeat_does_not_create_repeated_candidate():
    recorder = ShortcutRecorder("session-3")

    recorder.consume(event(VK_RMENU))
    recorder.consume(event(VK_RMENU))
    recorder.consume(event(VK_RMENU, down=False))
    recorder.consume(event(VK_RMENU, down=False))

    assert recorder.candidate == parse_shortcut("RAlt")


def test_preheld_keys_must_be_released_before_a_fresh_attempt():
    recorder = ShortcutRecorder("session-4", held_keys={VK_LCONTROL})

    recorder.consume(event(0x4B))
    assert recorder.candidate is None
    assert recorder.awaiting_release is True

    recorder.consume(event(VK_LCONTROL, down=False))
    recorder.consume(event(0x4B))
    recorder.consume(event(0x4B, down=False))
    recorder.consume(event(0x4B))

    assert recorder.candidate == parse_shortcut("K")


def test_invalid_unknown_key_explanation_survives_release_and_retry():
    recorder = ShortcutRecorder("session-5")

    recorder.consume(event(0xFF))
    assert recorder.candidate is None
    assert recorder.explanation == "This key isn't supported. Try another key."
    recorder.consume(event(0xFF, down=False))
    assert recorder.explanation == "This key isn't supported. Try another key."

    recorder.consume(event(0x41))

    assert recorder.explanation is None
    assert recorder.candidate == parse_shortcut("A")


def test_ambiguous_modifier_is_rejected_without_a_left_side_fallback():
    recorder = ShortcutRecorder("session-6")
    ambiguous = event(
        VK_CONTROL,
        raw_vk=VK_CONTROL,
        modifier_family=Modifier.CTRL,
        resolved_vk=None,
    )

    recorder.consume(ambiguous)

    assert recorder.candidate is None
    assert recorder.explanation == (
        "Couldn't identify which Ctrl key was pressed. Try again."
    )
    assert recorder.held_preview == "Ctrl"


def test_two_modifiers_are_not_rejected_before_a_valid_trigger():
    recorder = ShortcutRecorder("session-7")
    recorder.consume(event(VK_LCONTROL))
    recorder.consume(event(VK_RSHIFT))

    assert recorder.explanation is None

    recorder.consume(event(0x4B))

    assert recorder.candidate == parse_shortcut("LCtrl+RShift+K")


def test_modifier_only_chord_explains_after_all_keys_are_released():
    recorder = ShortcutRecorder("session-8")
    recorder.consume(event(VK_LCONTROL))
    recorder.consume(event(VK_LSHIFT))
    recorder.consume(event(VK_LSHIFT, down=False))
    recorder.consume(event(VK_LCONTROL, down=False))

    assert recorder.candidate is None
    assert recorder.explanation == "Use one trigger key, or tap a single modifier."
    assert recorder.awaiting_release is False


def test_intervening_key_disqualifies_a_standalone_modifier_even_when_released_first():
    recorder = ShortcutRecorder("session-9")
    recorder.consume(event(VK_RMENU))
    recorder.consume(event(VK_LSHIFT))
    recorder.consume(event(VK_LSHIFT, down=False))
    recorder.consume(event(VK_RMENU, down=False))

    assert recorder.candidate is None
    assert recorder.explanation == "Use one trigger key, or tap a single modifier."


def test_media_key_from_a_fn_result_is_recorded_and_no_event_is_invented():
    recorder = ShortcutRecorder("session-10")
    recorder.consume(event(0xAF))

    assert recorder.candidate == parse_shortcut("VolumeUp")

    waiting = ShortcutRecorder("session-11")
    assert waiting.candidate is None
    assert waiting.explanation is None
