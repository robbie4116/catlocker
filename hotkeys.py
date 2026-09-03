from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ShortcutError(ValueError):
    pass


class Modifier(Enum):
    CTRL = "Ctrl"
    ALT = "Alt"
    SHIFT = "Shift"
    WIN = "Win"


VK_LSHIFT, VK_RSHIFT = 0xA0, 0xA1
VK_LCONTROL, VK_RCONTROL = 0xA2, 0xA3
VK_LMENU, VK_RMENU = 0xA4, 0xA5
VK_LWIN, VK_RWIN = 0x5B, 0x5C

MODIFIER_VKS = {
    Modifier.CTRL: frozenset({VK_LCONTROL, VK_RCONTROL}),
    Modifier.ALT: frozenset({VK_LMENU, VK_RMENU}),
    Modifier.SHIFT: frozenset({VK_LSHIFT, VK_RSHIFT}),
    Modifier.WIN: frozenset({VK_LWIN, VK_RWIN}),
}


MODIFIER_ORDER = (Modifier.CTRL, Modifier.ALT, Modifier.SHIFT, Modifier.WIN)
MODIFIER_NAMES = {item.value.casefold(): item for item in MODIFIER_ORDER}

VK_NAMES = {
    **{chr(vk): vk for vk in range(0x41, 0x5B)},
    **{str(vk - 0x30): vk for vk in range(0x30, 0x3A)},
    **{f"F{number}": 0x6F + number for number in range(1, 25)},
    "Tab": 0x09,
    "Escape": 0x1B,
    "Space": 0x20,
    "PageUp": 0x21,
    "PageDown": 0x22,
    "End": 0x23,
    "Home": 0x24,
    "Left": 0x25,
    "Up": 0x26,
    "Right": 0x27,
    "Down": 0x28,
    "Insert": 0x2D,
    "Delete": 0x2E,
    "VolumeMute": 0xAD,
    "VolumeDown": 0xAE,
    "VolumeUp": 0xAF,
    "MediaNext": 0xB0,
    "MediaPrevious": 0xB1,
    "MediaStop": 0xB2,
    "MediaPlayPause": 0xB3,
}
TOKEN_ALIASES = {
    "volume_mute": "VolumeMute",
    "volume_down": "VolumeDown",
    "volume_up": "VolumeUp",
    "media_next": "MediaNext",
    "media_previous": "MediaPrevious",
    "media_stop": "MediaStop",
    "media_play_pause": "MediaPlayPause",
}
TOKEN_TO_NAME = {
    **{name.casefold(): name for name in VK_NAMES},
    **TOKEN_ALIASES,
}


@dataclass(frozen=True, slots=True)
class Shortcut:
    modifiers: frozenset[Modifier]
    trigger_vk: int
    trigger_name: str

    @property
    def canonical(self) -> str:
        parts = [item.value for item in MODIFIER_ORDER if item in self.modifiers]
        return "+".join([*parts, self.trigger_name])

    def matches(self, pressed: set[int], trigger_vk: int) -> bool:
        return (
            trigger_vk == self.trigger_vk
            and active_modifier_families(pressed) == self.modifiers
        )


def active_modifier_families(pressed: set[int]) -> frozenset[Modifier]:
    return frozenset(family for family, vks in MODIFIER_VKS.items() if pressed & vks)


@dataclass(frozen=True, slots=True)
class ValidationResult:
    shortcut: Shortcut
    warnings: tuple[str, ...] = ()


SECURE_SHORTCUTS = {
    (frozenset({Modifier.CTRL, Modifier.ALT}), 0x2E),
}
SYSTEM_SHORTCUTS = {
    (frozenset({Modifier.ALT}), 0x09),
    (frozenset({Modifier.WIN}), 0x4C),
    (frozenset({Modifier.WIN}), 0x52),
    (frozenset({Modifier.WIN, Modifier.SHIFT}), 0x53),
}
SYSTEM_WARNING = "This is a Windows system shortcut and may have surprising behavior."


def validate_shortcut(shortcut: Shortcut) -> ValidationResult:
    signature = (shortcut.modifiers, shortcut.trigger_vk)
    if signature in SECURE_SHORTCUTS:
        raise ShortcutError("Windows secure shortcuts cannot be intercepted.")
    warnings = (SYSTEM_WARNING,) if signature in SYSTEM_SHORTCUTS else ()
    return ValidationResult(shortcut, warnings)


def parse_shortcut(raw: str) -> Shortcut:
    pieces = raw.split("+")
    tokens = [token.strip() for token in pieces]
    if not tokens or not any(tokens):
        raise ShortcutError("Enter a shortcut.")
    if any(not token for token in tokens):
        raise ShortcutError("A shortcut cannot contain an empty key.")
    folded = [token.casefold() for token in tokens]
    if len(folded) != len(set(folded)):
        raise ShortcutError("A key may appear only once.")
    modifiers = frozenset(MODIFIER_NAMES[token] for token in folded if token in MODIFIER_NAMES)
    triggers = [token for token in folded if token not in MODIFIER_NAMES]
    if len(triggers) != 1:
        raise ShortcutError("Use modifier keys plus exactly one trigger key.")
    try:
        trigger_name = TOKEN_TO_NAME[triggers[0]]
    except KeyError as exc:
        raise ShortcutError(f"Unsupported key: {triggers[0]}") from exc
    return Shortcut(modifiers, VK_NAMES[trigger_name], trigger_name)
