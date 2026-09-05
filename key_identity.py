from __future__ import annotations

from hotkeys import (
    GENERIC_MODIFIER_VKS,
    KeyEvent,
    Modifier,
    VK_CONTROL,
    VK_LCONTROL,
    VK_LMENU,
    VK_LSHIFT,
    VK_MENU,
    VK_RCONTROL,
    VK_RMENU,
    VK_RSHIFT,
    VK_SHIFT,
    VK_TO_MODIFIER,
)


LLKHF_EXTENDED = 0x01
LLKHF_LOWER_IL = 0x02
LLKHF_INJECTED = 0x10
LLKHF_ALTDOWN = 0x20
LLKHF_UP = 0x80
_KNOWN_METADATA_FLAGS = (
    LLKHF_EXTENDED
    | LLKHF_LOWER_IL
    | LLKHF_INJECTED
    | LLKHF_ALTDOWN
    | LLKHF_UP
)


def _unresolved(raw_vk: int, family: Modifier, **kwargs) -> KeyEvent:
    return KeyEvent(
        vk=raw_vk,
        raw_vk=raw_vk,
        resolved_vk=None,
        modifier_family=family,
        **kwargs,
    )


def normalize_key_event(
    raw_vk: int,
    *,
    scan_code: int,
    flags: int,
    is_keydown: bool,
    injected: bool | None = None,
) -> KeyEvent:
    """Normalize one KBDLLHOOKSTRUCT identity without consulting Tk or a layout."""

    raw_vk = int(raw_vk)
    scan_code = int(scan_code)
    flags = int(flags)
    if injected is None:
        injected = bool(flags & LLKHF_INJECTED)
    common = {
        "is_keydown": bool(is_keydown),
        "injected": bool(injected),
        "scan_code": scan_code,
        "flags": flags,
    }

    if raw_vk in VK_TO_MODIFIER:
        family = VK_TO_MODIFIER[raw_vk]
        return KeyEvent(
            vk=raw_vk,
            raw_vk=raw_vk,
            resolved_vk=raw_vk,
            modifier_family=family,
            **common,
        )

    family = GENERIC_MODIFIER_VKS.get(raw_vk)
    if family is None:
        return KeyEvent(
            vk=raw_vk,
            raw_vk=raw_vk,
            resolved_vk=raw_vk,
            **common,
        )

    if flags & ~_KNOWN_METADATA_FLAGS:
        return _unresolved(raw_vk, family, **common)
    if is_keydown and flags & LLKHF_UP:
        return _unresolved(raw_vk, family, **common)

    extended = bool(flags & LLKHF_EXTENDED)
    resolved = None
    if raw_vk == VK_CONTROL and scan_code == 0x1D:
        resolved = VK_RCONTROL if extended else VK_LCONTROL
    elif raw_vk == VK_MENU and scan_code == 0x38:
        resolved = VK_RMENU if extended else VK_LMENU
    elif raw_vk == VK_SHIFT:
        if scan_code == 0x2A and not extended:
            resolved = VK_LSHIFT
        elif scan_code == 0x36:
            resolved = VK_RSHIFT

    if resolved is None:
        return _unresolved(raw_vk, family, **common)
    return KeyEvent(
        vk=resolved,
        raw_vk=raw_vk,
        resolved_vk=resolved,
        modifier_family=family,
        **common,
    )


NormalizedKeyEvent = KeyEvent


__all__ = [
    "LLKHF_ALTDOWN",
    "LLKHF_EXTENDED",
    "LLKHF_INJECTED",
    "LLKHF_LOWER_IL",
    "LLKHF_UP",
    "NormalizedKeyEvent",
    "normalize_key_event",
]
