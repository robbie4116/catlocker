import pytest

from hotkeys import (
    Modifier,
    VK_CONTROL,
    VK_LCONTROL,
    VK_LMENU,
    VK_LSHIFT,
    VK_RCONTROL,
    VK_RMENU,
    VK_RSHIFT,
    VK_SHIFT,
)
from key_identity import (
    LLKHF_ALTDOWN,
    LLKHF_EXTENDED,
    LLKHF_LOWER_IL,
    LLKHF_UP,
    normalize_key_event,
)


@pytest.mark.parametrize(
    ("raw_vk", "resolved_vk", "family"),
    [
        (VK_LCONTROL, VK_LCONTROL, Modifier.CTRL),
        (VK_RCONTROL, VK_RCONTROL, Modifier.CTRL),
        (VK_LMENU, VK_LMENU, Modifier.ALT),
        (VK_RMENU, VK_RMENU, Modifier.ALT),
        (VK_LSHIFT, VK_LSHIFT, Modifier.SHIFT),
        (VK_RSHIFT, VK_RSHIFT, Modifier.SHIFT),
    ],
)
def test_explicit_modifier_virtual_keys_win_over_native_metadata(
    raw_vk, resolved_vk, family
):
    event = normalize_key_event(
        raw_vk,
        scan_code=0x36,
        flags=LLKHF_EXTENDED,
        is_keydown=True,
    )

    assert event.vk == resolved_vk
    assert event.raw_vk == raw_vk
    assert event.resolved_vk == resolved_vk
    assert event.modifier_family is family
    assert event.is_resolved_modifier is True


@pytest.mark.parametrize(
    ("raw_vk", "scan_code", "flags", "resolved_vk"),
    [
        (VK_CONTROL, 0x1D, 0, VK_LCONTROL),
        (VK_CONTROL, 0x1D, LLKHF_EXTENDED, VK_RCONTROL),
        (0x12, 0x38, LLKHF_ALTDOWN, VK_LMENU),
        (0x12, 0x38, LLKHF_EXTENDED, VK_RMENU),
        (VK_SHIFT, 0x2A, 0, VK_LSHIFT),
        (VK_SHIFT, 0x36, LLKHF_EXTENDED | LLKHF_UP, VK_RSHIFT),
        (VK_CONTROL, 0x1D, LLKHF_LOWER_IL | LLKHF_UP, VK_LCONTROL),
    ],
)
def test_generic_modifier_events_resolve_from_valid_scan_metadata(
    raw_vk, scan_code, flags, resolved_vk
):
    event = normalize_key_event(
        raw_vk,
        scan_code=scan_code,
        flags=flags,
        is_keydown=False,
    )

    assert event.vk == resolved_vk
    assert event.resolved_vk == resolved_vk
    assert event.is_keydown is False


@pytest.mark.parametrize(
    ("raw_vk", "scan_code", "flags", "family"),
    [
        (VK_CONTROL, 0, 0, Modifier.CTRL),
        (VK_CONTROL, 0x1D, LLKHF_EXTENDED | 0x04, Modifier.CTRL),
        (0x12, 0x38, 0x04, Modifier.ALT),
        (VK_SHIFT, 0x2A, LLKHF_EXTENDED, Modifier.SHIFT),
        (VK_SHIFT, 0x00, 0, Modifier.SHIFT),
        (VK_CONTROL, 0x1D, LLKHF_UP, Modifier.CTRL),
    ],
)
def test_invalid_or_inconsistent_modifier_metadata_stays_unresolved(
    raw_vk, scan_code, flags, family
):
    event = normalize_key_event(
        raw_vk,
        scan_code=scan_code,
        flags=flags,
        is_keydown=True,
    )

    assert event.vk == raw_vk
    assert event.resolved_vk is None
    assert event.modifier_family is family
    assert event.is_resolved_modifier is False


def test_non_modifier_identity_is_preserved_without_modifier_metadata():
    event = normalize_key_event(0xC0, scan_code=0x29, flags=0, is_keydown=True)

    assert event.vk == 0xC0
    assert event.raw_vk == 0xC0
    assert event.resolved_vk == 0xC0
    assert event.modifier_family is None
