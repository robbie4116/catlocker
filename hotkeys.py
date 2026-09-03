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
