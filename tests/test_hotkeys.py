import pytest

from hotkeys import Shortcut, ShortcutError, parse_shortcut
from hotkeys import (
    Modifier,
    VK_LCONTROL,
    VK_LMENU,
    VK_LSHIFT,
    VK_LWIN,
    VK_RCONTROL,
    active_modifier_families,
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


def test_recorded_physical_keys_normalize_to_generic_modifier_families():
    shortcut = shortcut_from_pressed_vks(
        {VK_RCONTROL, 0xA1, 0x4B},
        trigger_vk=0x4B,
    )
    assert shortcut.canonical == "Ctrl+Shift+K"


def test_recorder_rejects_modifier_only_and_unknown_trigger():
    with pytest.raises(ShortcutError):
        shortcut_from_pressed_vks({VK_LCONTROL}, trigger_vk=VK_LCONTROL)
    with pytest.raises(ShortcutError):
        shortcut_from_pressed_vks({0xFF}, trigger_vk=0xFF)


def test_recorder_rejects_an_extra_non_modifier_key():
    with pytest.raises(ShortcutError):
        shortcut_from_pressed_vks({0x41, 0x4B}, trigger_vk=0x4B)
