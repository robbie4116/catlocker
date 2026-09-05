import hotkeys
import pytest

from hotkeys import Shortcut, ShortcutError, parse_shortcut
from hotkeys import (
    ActivationKind,
    Modifier,
    VK_LCONTROL,
    VK_LMENU,
    VK_LSHIFT,
    VK_LWIN,
    VK_RCONTROL,
    active_modifier_families,
    format_shortcut,
    shortcut_from_pressed_vks,
    validate_shortcut,
)


@pytest.mark.parametrize(
    ("raw", "canonical", "trigger"),
    [
        ("f24", "F24", 0x87),
        ("shift+ctrl+k", "Ctrl+Shift+K", 0x4B),
        ("CTRL + alt + f12", "Ctrl+Alt+F12", 0x7B),
        ("volume_up", "VolumeUp", 0xAF),
    ],
)
def test_parse_shortcut_normalizes_names_and_order(raw, canonical, trigger):
    shortcut = parse_shortcut(raw)
    assert shortcut.canonical == canonical
    assert shortcut.trigger_vk == trigger


def test_shortcut_is_immutable_and_uses_modifier_families():
    shortcut = parse_shortcut("ctrl+shift+k")
    assert shortcut == Shortcut(frozenset({Modifier.CTRL, Modifier.SHIFT}), 0x4B, "K")
    with pytest.raises(AttributeError):
        shortcut.trigger_vk = 1


@pytest.mark.parametrize("raw", ["", "ctrl", "ctrl+ctrl+k", "k+l", "wat"])
def test_parse_shortcut_rejects_invalid_shape(raw):
    with pytest.raises(ShortcutError):
        parse_shortcut(raw)


@pytest.mark.parametrize(
    ("name", "vk"),
    [(f"F{number}", 0x6F + number) for number in range(13, 25)],
)
def test_function_keys_through_f24_are_supported(name, vk):
    assert parse_shortcut(name).trigger_vk == vk


@pytest.mark.parametrize(
    ("name", "vk"),
    [
        ("VolumeMute", 0xAD),
        ("VolumeDown", 0xAE),
        ("VolumeUp", 0xAF),
        ("MediaNext", 0xB0),
        ("MediaPrevious", 0xB1),
        ("MediaStop", 0xB2),
        ("MediaPlayPause", 0xB3),
    ],
)
def test_documented_media_and_volume_keys_are_supported(name, vk):
    assert parse_shortcut(name).trigger_vk == vk


@pytest.mark.parametrize("raw", ["Ctrl++K", "+K", "K+"])
def test_parse_shortcut_rejects_empty_segments(raw):
    with pytest.raises(ShortcutError, match="empty"):
        parse_shortcut(raw)


def test_generic_ctrl_matches_either_or_both_physical_control_keys():
    assert active_modifier_families({VK_LCONTROL}) == frozenset({Modifier.CTRL})
    assert active_modifier_families({VK_RCONTROL}) == frozenset({Modifier.CTRL})
    assert active_modifier_families({VK_LCONTROL, VK_RCONTROL}) == frozenset({Modifier.CTRL})


@pytest.mark.parametrize(
    ("vk", "family"),
    [
        (VK_LCONTROL, Modifier.CTRL),
        (VK_RCONTROL, Modifier.CTRL),
        (VK_LMENU, Modifier.ALT),
        (0xA5, Modifier.ALT),
        (VK_LSHIFT, Modifier.SHIFT),
        (0xA1, Modifier.SHIFT),
        (VK_LWIN, Modifier.WIN),
        (0x5C, Modifier.WIN),
    ],
)
def test_each_physical_modifier_maps_to_its_family(vk, family):
    assert active_modifier_families({vk}) == frozenset({family})


@pytest.mark.parametrize("raw", ["ctrl+alt+delete"])
def test_secure_sequence_is_rejected(raw):
    with pytest.raises(ShortcutError, match="secure"):
        validate_shortcut(parse_shortcut(raw))


@pytest.mark.parametrize(
    "raw",
    ["lctrl+rctrl", "leftctrl+rightctrl", "ctrl_l+ctrl_r"],
)
def test_emergency_chord_spellings_cannot_be_configured(raw):
    with pytest.raises(ShortcutError):
        parse_shortcut(raw)


@pytest.mark.parametrize(
    ("raw", "warning"),
    [
        ("alt+tab", "Windows system shortcut"),
        ("win+l", "Windows system shortcut"),
        ("win+r", "Windows system shortcut"),
        ("win+shift+s", "Windows system shortcut"),
    ],
)
def test_system_shortcuts_require_confirmation(raw, warning):
    result = validate_shortcut(parse_shortcut(raw))
    assert any(warning in message for message in result.warnings)


def test_extra_modifier_family_prevents_a_match():
    shortcut = parse_shortcut("ctrl+shift+k")
    assert shortcut.matches({VK_LCONTROL, VK_LSHIFT}, 0x4B)
    assert not shortcut.matches({VK_LCONTROL, VK_LSHIFT, VK_LMENU}, 0x4B)


def test_recorded_physical_keys_preserve_modifier_sides():
    shortcut = shortcut_from_pressed_vks(
        {VK_RCONTROL, 0xA1, 0x4B},
        trigger_vk=0x4B,
    )
    assert shortcut.canonical == "RCtrl+RShift+K"


def test_recorder_accepts_one_side_specific_modifier_and_rejects_unknown_trigger():
    assert shortcut_from_pressed_vks(
        {VK_LCONTROL}, trigger_vk=VK_LCONTROL
    ).canonical == "LCtrl"
    with pytest.raises(ShortcutError):
        shortcut_from_pressed_vks({0xFF}, trigger_vk=0xFF)


def test_recorder_rejects_an_extra_non_modifier_key():
    with pytest.raises(ShortcutError):
        shortcut_from_pressed_vks({0x41, 0x4B}, trigger_vk=0x4B)


def test_format_pressed_vks_orders_modifiers_and_deduplicates_families():
    formatter = getattr(hotkeys, "format_pressed_vks")

    assert formatter(
        {hotkeys.VK_LCONTROL, hotkeys.VK_RCONTROL, hotkeys.VK_LSHIFT, 0x4B}
    ) == "Ctrl+Shift+K"


def test_format_pressed_vks_labels_unknown_virtual_keys():
    formatter = getattr(hotkeys, "format_pressed_vks")

    assert formatter({0xFF}) == "VK_FF"


def test_format_pressed_vks_returns_empty_for_no_pressed_keys():
    formatter = getattr(hotkeys, "format_pressed_vks")

    assert formatter(set()) == ""


def test_format_pressed_vks_sorts_mixed_known_and_unknown_keys_by_vk():
    formatter = getattr(hotkeys, "format_pressed_vks")

    assert formatter({0x100, 0xFF, 0x4B, 0x41}) == "A+K+VK_FF+VK_100"


def test_format_pressed_vks_uses_full_modifier_order():
    formatter = getattr(hotkeys, "format_pressed_vks")
    pressed = {
        hotkeys.VK_LCONTROL,
        hotkeys.VK_LMENU,
        hotkeys.VK_LSHIFT,
        hotkeys.VK_LWIN,
        0x4B,
    }

    assert formatter(pressed) == "Ctrl+Alt+Shift+Win+K"


def test_side_specific_shortcut_round_trip():
    shortcut = parse_shortcut("LCtrl + K")

    assert shortcut.canonical == "LCtrl+K"
    assert parse_shortcut(shortcut.canonical) == shortcut
    assert format_shortcut(shortcut) == "LCtrl + K"


def test_legacy_shortcut_keeps_generic_tokens():
    shortcut = parse_shortcut("Ctrl+Shift+K")

    assert shortcut.canonical == "Ctrl+Shift+K"
    assert format_shortcut(shortcut) == "Ctrl + Shift + K"


@pytest.mark.parametrize(
    ("token", "vk"),
    [
        ("LCtrl", VK_LCONTROL),
        ("RCtrl", VK_RCONTROL),
        ("LAlt", VK_LMENU),
        ("RAlt", 0xA5),
        ("LShift", VK_LSHIFT),
        ("RShift", 0xA1),
        ("LWin", VK_LWIN),
        ("RWin", 0x5C),
    ],
)
def test_all_side_specific_modifiers_parse_as_exact_requirements(token, vk):
    shortcut = parse_shortcut(token)

    assert shortcut.is_standalone is True
    assert shortcut.activation_kind is ActivationKind.RELEASE
    assert shortcut.trigger_vk == vk
    assert shortcut.canonical == token
    assert format_shortcut(shortcut) == token


def test_both_sides_of_one_family_are_ordered_before_the_trigger():
    shortcut = parse_shortcut("K+RCtrl+LCtrl")

    assert shortcut.canonical == "LCtrl+RCtrl+K"
    assert shortcut.matches({VK_LCONTROL, VK_RCONTROL}, 0x4B)
    assert not shortcut.matches({VK_LCONTROL}, 0x4B)


def test_exact_side_matching_rejects_wrong_side_both_sides_and_extra_family():
    shortcut = parse_shortcut("LCtrl+K")

    assert shortcut.matches({VK_LCONTROL}, 0x4B)
    assert not shortcut.matches({VK_RCONTROL}, 0x4B)
    assert not shortcut.matches({VK_LCONTROL, VK_RCONTROL}, 0x4B)
    assert not shortcut.matches({VK_LCONTROL, VK_LSHIFT}, 0x4B)


def test_legacy_generic_matching_accepts_left_right_or_both_sides():
    shortcut = parse_shortcut("Ctrl+K")

    assert shortcut.matches({VK_LCONTROL}, 0x4B)
    assert shortcut.matches({VK_RCONTROL}, 0x4B)
    assert shortcut.matches({VK_LCONTROL, VK_RCONTROL}, 0x4B)


@pytest.mark.parametrize(
    ("raw", "canonical", "vk"),
    [
        ("OEM_1", "OEM_1", 0xBA),
        ("OEM_PLUS", "OEM_PLUS", 0xBB),
        ("OEM_COMMA", "OEM_COMMA", 0xBC),
        ("OEM_MINUS", "OEM_MINUS", 0xBD),
        ("OEM_PERIOD", "OEM_PERIOD", 0xBE),
        ("OEM_2", "OEM_2", 0xBF),
        ("OEM_3", "OEM_3", 0xC0),
        ("OEM_4", "OEM_4", 0xDB),
        ("OEM_5", "OEM_5", 0xDC),
        ("OEM_6", "OEM_6", 0xDD),
        ("OEM_7", "OEM_7", 0xDE),
        ("OEM_102", "OEM_102", 0xE2),
    ],
)
def test_oem_punctuation_round_trips_with_stable_tokens(raw, canonical, vk):
    shortcut = parse_shortcut(raw)

    assert shortcut.canonical == canonical
    assert shortcut.trigger_vk == vk
    assert parse_shortcut(shortcut.canonical) == shortcut


def test_shifted_punctuation_uses_shift_token_and_safe_oem_trigger():
    shortcut = parse_shortcut("LShift+OEM_PLUS")

    assert shortcut.canonical == "LShift+OEM_PLUS"
    assert "+" not in shortcut.trigger_name
    assert format_shortcut(shortcut) == "LShift + +"


@pytest.mark.parametrize("raw", ["Ctrl", "Alt", "Shift", "Win"])
def test_generic_standalone_modifiers_are_invalid(raw):
    with pytest.raises(ShortcutError, match="side"):
        parse_shortcut(raw)


@pytest.mark.parametrize("raw", ["Ctrl+LCtrl+K", "LCtrl+Ctrl+K"])
def test_generic_and_specific_same_family_are_ambiguous(raw):
    with pytest.raises(ShortcutError, match="ambiguous"):
        parse_shortcut(raw)


def test_exact_modifiers_in_different_families_can_mix_with_generic_family():
    shortcut = parse_shortcut("Ctrl+RAlt+K")

    assert shortcut.canonical == "Ctrl+RAlt+K"
    assert shortcut.matches({VK_LCONTROL, 0xA5}, 0x4B)
    assert not shortcut.matches({VK_RCONTROL, VK_LMENU}, 0x4B)


def test_side_specific_system_shortcut_still_warns_and_secure_shortcut_is_rejected():
    warning = validate_shortcut(parse_shortcut("RAlt+Tab"))
    assert warning.warnings == (hotkeys.SYSTEM_WARNING,)

    with pytest.raises(ShortcutError, match="secure"):
        validate_shortcut(parse_shortcut("LCtrl+RAlt+Delete"))
