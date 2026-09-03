import pytest

from hotkeys import Modifier, Shortcut, ShortcutError, parse_shortcut


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
