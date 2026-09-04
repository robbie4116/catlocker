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
VK_TO_NAME = {vk: name for name, vk in VK_NAMES.items()}
SUPPORTED_MODIFIER_VKS = frozenset().union(*MODIFIER_VKS.values())


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


@dataclass(frozen=True, slots=True)
class KeyEvent:
    vk: int
    is_keydown: bool
    injected: bool = False


@dataclass(frozen=True, slots=True)
class Transition:
    suppress: bool
    locked: bool
    changed: bool = False
    reason: str | None = None


class InputState:
    def __init__(self, shortcut: Shortcut, *, locked: bool = False):
        self.shortcut = shortcut
        self.locked = locked
        self.pressed: set[int] = set()
        self.passed_down: set[int] = set()
        self.suppressed_down: set[int] = set()
        self.toggle_latched = False
        self.emergency_latched = False
        self.recording = False

    def enter_recording(self) -> bool:
        if self.locked:
            return False
        self.recording = True
        return True

    def exit_recording(self) -> None:
        self.recording = False

    def replace_shortcut(self, shortcut: Shortcut) -> bool:
        if self.locked:
            return False
        self.exit_recording()
        self.shortcut = shortcut
        self.toggle_latched = shortcut.trigger_vk in self.pressed
        return True

    def set_locked(self, locked: bool) -> Transition:
        if locked:
            self.exit_recording()
        changed = self.locked != locked
        self.locked = locked
        return Transition(False, self.locked, changed, "command" if changed else None)

    def handle(self, event: KeyEvent) -> Transition:
        return self._handle_down(event) if event.is_keydown else self._handle_up(event)

    def _repeat_is_suppressed(self, vk: int) -> bool:
        return vk not in self.passed_down or self.locked

    def _release_is_suppressed(self, vk: int) -> bool:
        if vk in self.suppressed_down:
            return True
        if vk in self.passed_down:
            return False
        return self.locked

    def _handle_down(self, event: KeyEvent) -> Transition:
        if event.vk in self.pressed:
            return Transition(self._repeat_is_suppressed(event.vk), self.locked)

        self.pressed.add(event.vk)
        previous_locked = self.locked
        reason = None
        if (
            {VK_LCONTROL, VK_RCONTROL} <= self.pressed
            and not self.emergency_latched
        ):
            self.emergency_latched = True
            self.locked = False
            suppress = True
            reason = "emergency"
        elif (
            not self.recording
            and not self.toggle_latched
            and self.shortcut.matches(self.pressed, event.vk)
        ):
            self.toggle_latched = True
            self.locked = not self.locked
            suppress = True
            reason = "toggle"
        else:
            suppress = self.locked

        if suppress:
            self.suppressed_down.add(event.vk)
        else:
            self.passed_down.add(event.vk)
        return Transition(suppress, self.locked, self.locked != previous_locked, reason)

    def _handle_up(self, event: KeyEvent) -> Transition:
        suppress = self._release_is_suppressed(event.vk)
        self.pressed.discard(event.vk)
        self.passed_down.discard(event.vk)
        self.suppressed_down.discard(event.vk)
        if event.vk == self.shortcut.trigger_vk:
            self.toggle_latched = False
        if event.vk in {VK_LCONTROL, VK_RCONTROL}:
            self.emergency_latched = False
        return Transition(suppress, self.locked)


def active_modifier_families(pressed: set[int]) -> frozenset[Modifier]:
    return frozenset(family for family, vks in MODIFIER_VKS.items() if pressed & vks)


def format_pressed_vks(pressed: set[int]) -> str:
    active_modifiers = active_modifier_families(pressed)
    parts = [family.value for family in MODIFIER_ORDER if family in active_modifiers]
    non_modifiers = sorted(set(pressed) - SUPPORTED_MODIFIER_VKS)
    parts.extend(VK_TO_NAME.get(vk, f"VK_{vk:02X}") for vk in non_modifiers)
    return "+".join(parts)


def shortcut_from_pressed_vks(pressed: set[int], *, trigger_vk: int) -> Shortcut:
    pressed_vks = set(pressed)
    if trigger_vk in SUPPORTED_MODIFIER_VKS:
        raise ShortcutError("A modifier cannot be the trigger key.")
    if trigger_vk not in pressed_vks:
        raise ShortcutError("The trigger key must be pressed.")
    try:
        trigger_name = VK_TO_NAME[trigger_vk]
    except KeyError as exc:
        raise ShortcutError(f"Unsupported key: {trigger_vk}") from exc

    extra_non_modifiers = pressed_vks - SUPPORTED_MODIFIER_VKS - {trigger_vk}
    if extra_non_modifiers:
        raise ShortcutError("Press exactly one non-modifier trigger key.")

    shortcut = Shortcut(
        active_modifier_families(pressed_vks),
        trigger_vk,
        trigger_name,
    )
    return validate_shortcut(shortcut).shortcut


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
