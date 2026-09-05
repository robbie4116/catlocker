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


class ActivationKind(Enum):
    TRIGGER_DOWN = "trigger_down"
    RELEASE = "release"


VK_SHIFT, VK_CONTROL, VK_MENU = 0x10, 0x11, 0x12
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
GENERIC_MODIFIER_VKS = {
    VK_CONTROL: Modifier.CTRL,
    VK_MENU: Modifier.ALT,
    VK_SHIFT: Modifier.SHIFT,
}

MODIFIER_ORDER = (Modifier.CTRL, Modifier.ALT, Modifier.SHIFT, Modifier.WIN)
MODIFIER_NAMES = {item.value.casefold(): item for item in MODIFIER_ORDER}
SPECIFIC_MODIFIER_NAMES = {
    "lctrl": VK_LCONTROL,
    "leftctrl": VK_LCONTROL,
    "ctrl_l": VK_LCONTROL,
    "control_l": VK_LCONTROL,
    "rctrl": VK_RCONTROL,
    "rightctrl": VK_RCONTROL,
    "ctrl_r": VK_RCONTROL,
    "control_r": VK_RCONTROL,
    "lalt": VK_LMENU,
    "leftalt": VK_LMENU,
    "alt_l": VK_LMENU,
    "ralt": VK_RMENU,
    "rightalt": VK_RMENU,
    "alt_r": VK_RMENU,
    "lshift": VK_LSHIFT,
    "leftshift": VK_LSHIFT,
    "shift_l": VK_LSHIFT,
    "rshift": VK_RSHIFT,
    "rightshift": VK_RSHIFT,
    "shift_r": VK_RSHIFT,
    "lwin": VK_LWIN,
    "leftwin": VK_LWIN,
    "win_l": VK_LWIN,
    "super_l": VK_LWIN,
    "meta_l": VK_LWIN,
    "rwin": VK_RWIN,
    "rightwin": VK_RWIN,
    "win_r": VK_RWIN,
    "super_r": VK_RWIN,
    "meta_r": VK_RWIN,
}
MODIFIER_VK_NAMES = {
    VK_LCONTROL: "LCtrl",
    VK_RCONTROL: "RCtrl",
    VK_LMENU: "LAlt",
    VK_RMENU: "RAlt",
    VK_LSHIFT: "LShift",
    VK_RSHIFT: "RShift",
    VK_LWIN: "LWin",
    VK_RWIN: "RWin",
}
VK_TO_MODIFIER = {
    vk: family for family, vks in MODIFIER_VKS.items() for vk in vks
}

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
    "OEM_1": 0xBA,
    "OEM_PLUS": 0xBB,
    "OEM_COMMA": 0xBC,
    "OEM_MINUS": 0xBD,
    "OEM_PERIOD": 0xBE,
    "OEM_2": 0xBF,
    "OEM_3": 0xC0,
    "OEM_4": 0xDB,
    "OEM_5": 0xDC,
    "OEM_6": 0xDD,
    "OEM_7": 0xDE,
    "OEM_102": 0xE2,
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
ALL_MODIFIER_VKS = SUPPORTED_MODIFIER_VKS | frozenset(GENERIC_MODIFIER_VKS)
OEM_DISPLAY_NAMES = {
    "OEM_1": ";",
    "OEM_PLUS": "+",
    "OEM_COMMA": ",",
    "OEM_MINUS": "-",
    "OEM_PERIOD": ".",
    "OEM_2": "/",
    "OEM_3": "Backtick",
    "OEM_4": "[",
    "OEM_5": "\\",
    "OEM_6": "]",
    "OEM_7": "'",
    "OEM_102": "OEM key 102",
}


def modifier_family_for_vk(vk: int) -> Modifier | None:
    return VK_TO_MODIFIER.get(int(vk)) or GENERIC_MODIFIER_VKS.get(int(vk))


def _modifier_vks_for_family(pressed: set[int], family: Modifier) -> frozenset[int]:
    return frozenset(int(vk) for vk in pressed if int(vk) in MODIFIER_VKS[family])


@dataclass(frozen=True, slots=True)
class Shortcut:
    """A persisted shortcut, retaining generic legacy families separately from sides."""

    modifiers: frozenset[Modifier]
    trigger_vk: int
    trigger_name: str
    exact_modifiers: frozenset[int] = frozenset()
    activation_kind: ActivationKind = ActivationKind.TRIGGER_DOWN

    @property
    def is_standalone(self) -> bool:
        return self.activation_kind is ActivationKind.RELEASE

    @property
    def canonical(self) -> str:
        return "+".join(_shortcut_tokens(self))

    def matches(self, pressed: set[int], trigger_vk: int) -> bool:
        if self.activation_kind is not ActivationKind.TRIGGER_DOWN:
            return False
        if int(trigger_vk) != self.trigger_vk:
            return False

        active_families = active_modifier_families(pressed)
        required_families = set(self.modifiers)
        required_families.update(
            family
            for family in MODIFIER_ORDER
            if self.exact_modifiers & MODIFIER_VKS[family]
        )
        if active_families != frozenset(required_families):
            return False

        for family in MODIFIER_ORDER:
            exact = self.exact_modifiers & MODIFIER_VKS[family]
            if exact and _modifier_vks_for_family(pressed, family) != exact:
                return False
        return True


@dataclass(frozen=True, slots=True)
class KeyEvent:
    vk: int
    is_keydown: bool
    injected: bool = False
    raw_vk: int | None = None
    scan_code: int = 0
    flags: int = 0
    resolved_vk: int | None = None
    modifier_family: Modifier | None = None

    @property
    def is_resolved_modifier(self) -> bool:
        if self.modifier_family is None:
            return False
        resolved = self.resolved_vk
        if resolved is None:
            resolved = self.vk if self.vk in SUPPORTED_MODIFIER_VKS else None
        return resolved is not None


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
        self._tap_tracker = None

    def _reset_taps(self) -> None:
        if self._tap_tracker is not None:
            self._tap_tracker.reset()

    def enter_recording(self) -> bool:
        if self.locked:
            return False
        self.recording = True
        self._reset_taps()
        return True

    def exit_recording(self) -> None:
        self.recording = False
        self._reset_taps()

    def replace_shortcut(self, shortcut: Shortcut) -> bool:
        if self.locked:
            return False
        self.exit_recording()
        self.shortcut = shortcut
        self.toggle_latched = shortcut.trigger_vk in self.pressed
        self._ensure_tap_tracker()
        self._reset_taps()
        return True

    def set_locked(self, locked: bool) -> Transition:
        if locked:
            self.exit_recording()
        else:
            self._reset_taps()
        changed = self.locked != locked
        self.locked = locked
        return Transition(False, self.locked, changed, "command" if changed else None)

    def handle(self, event: KeyEvent) -> Transition:
        return self._handle_down(event) if event.is_keydown else self._handle_up(event)

    def _ensure_tap_tracker(self) -> None:
        if self._tap_tracker is None:
            from modifier_tap import ModifierTapTracker

            self._tap_tracker = ModifierTapTracker(
                self.shortcut.trigger_vk if self.shortcut.is_standalone else None
            )
        else:
            self._tap_tracker.set_modifier(
                self.shortcut.trigger_vk if self.shortcut.is_standalone else None
            )

    def _repeat_is_suppressed(self, vk: int) -> bool:
        return vk not in self.passed_down or self.locked

    def _release_is_suppressed(self, vk: int) -> bool:
        if vk in self.suppressed_down:
            return True
        if vk in self.passed_down:
            return False
        return self.locked

    def _handle_down(self, event: KeyEvent) -> Transition:
        self._ensure_tap_tracker()
        if event.vk in self.pressed:
            if self._tap_tracker is not None:
                self._tap_tracker.observe_down(event.vk, self.pressed, repeat=True)
            return Transition(self._repeat_is_suppressed(event.vk), self.locked)

        held_before = set(self.pressed)
        if self._tap_tracker is not None:
            self._tap_tracker.observe_down(event.vk, held_before, repeat=False)
        self.pressed.add(event.vk)
        previous_locked = self.locked
        reason = None
        if (
            {VK_LCONTROL, VK_RCONTROL} <= self.pressed
            and not self.emergency_latched
        ):
            self.emergency_latched = True
            self._reset_taps()
            self.locked = False
            suppress = True
            reason = "emergency"
        elif (
            not self.recording
            and not self.shortcut.is_standalone
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
        self._ensure_tap_tracker()
        suppress = self._release_is_suppressed(event.vk)
        eligible_release = False
        if self._tap_tracker is not None:
            eligible_release = self._tap_tracker.observe_up(event.vk)
        self.pressed.discard(event.vk)
        self.passed_down.discard(event.vk)
        self.suppressed_down.discard(event.vk)
        if event.vk == self.shortcut.trigger_vk:
            self.toggle_latched = False
        if event.vk in {VK_LCONTROL, VK_RCONTROL}:
            self.emergency_latched = False

        previous_locked = self.locked
        reason = None
        if (
            eligible_release
            and not self.recording
            and not self.emergency_latched
            and self.shortcut.is_standalone
            and event.vk == self.shortcut.trigger_vk
            and event.is_resolved_modifier
        ):
            self.locked = not self.locked
            reason = "toggle"
        return Transition(suppress, self.locked, self.locked != previous_locked, reason)

    def reset_runtime(self) -> None:
        self.pressed.clear()
        self.passed_down.clear()
        self.suppressed_down.clear()
        self.toggle_latched = False
        self.emergency_latched = False
        self.recording = False
        self._reset_taps()


def active_modifier_families(pressed: set[int]) -> frozenset[Modifier]:
    return frozenset(
        family
        for family, vks in MODIFIER_VKS.items()
        if pressed & vks
        or any(GENERIC_MODIFIER_VKS.get(int(vk)) is family for vk in pressed)
    )


def _shortcut_tokens(shortcut: Shortcut) -> list[str]:
    parts: list[str] = []
    for family in MODIFIER_ORDER:
        exact = shortcut.exact_modifiers & MODIFIER_VKS[family]
        if exact:
            parts.extend(MODIFIER_VK_NAMES[vk] for vk in sorted(exact))
        elif family in shortcut.modifiers:
            parts.append(family.value)
    parts.append(shortcut.trigger_name)
    return parts


def _display_label(name: str, resolver=None) -> str:
    if resolver is not None:
        resolved = resolver(name)
        if resolved:
            return str(resolved)
    return OEM_DISPLAY_NAMES.get(name, name)


def format_shortcut(shortcut: Shortcut, *, key_label_resolver=None) -> str:
    labels = [
        _display_label(token, key_label_resolver)
        if token == shortcut.trigger_name or token.startswith("OEM_")
        else token
        for token in _shortcut_tokens(shortcut)
    ]
    return " + ".join(labels)


def format_pressed_vks(
    pressed: set[int],
    *,
    preserve_modifier_sides: bool = False,
    key_label_resolver=None,
) -> str:
    parts: list[str] = []
    if preserve_modifier_sides:
        for family in MODIFIER_ORDER:
            parts.extend(
                MODIFIER_VK_NAMES[vk]
                for vk in sorted(pressed & MODIFIER_VKS[family])
            )
    else:
        active_modifiers = active_modifier_families(pressed)
        parts.extend(family.value for family in MODIFIER_ORDER if family in active_modifiers)
    non_modifiers = sorted(set(pressed) - ALL_MODIFIER_VKS)
    parts.extend(
        _display_label(VK_TO_NAME.get(vk, f"VK_{vk:02X}"), key_label_resolver)
        for vk in non_modifiers
    )
    return "+".join(parts)


def shortcut_from_pressed_vks(pressed: set[int], *, trigger_vk: int) -> Shortcut:
    pressed_vks = {int(vk) for vk in pressed}
    trigger_vk = int(trigger_vk)
    if trigger_vk in SUPPORTED_MODIFIER_VKS:
        if pressed_vks != {trigger_vk}:
            raise ShortcutError("Use one trigger key, or tap a single modifier.")
        shortcut = Shortcut(
            frozenset(),
            trigger_vk,
            MODIFIER_VK_NAMES[trigger_vk],
            activation_kind=ActivationKind.RELEASE,
        )
        return validate_shortcut(shortcut).shortcut
    if trigger_vk in ALL_MODIFIER_VKS:
        raise ShortcutError("Couldn't identify which modifier key was pressed.")
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
        frozenset(),
        trigger_vk,
        trigger_name,
        exact_modifiers=frozenset(pressed_vks & SUPPORTED_MODIFIER_VKS),
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


def _shortcut_families(shortcut: Shortcut) -> frozenset[Modifier]:
    families = set(shortcut.modifiers)
    families.update(
        family
        for family in MODIFIER_ORDER
        if shortcut.exact_modifiers & MODIFIER_VKS[family]
    )
    return frozenset(families)


def validate_shortcut(shortcut: Shortcut) -> ValidationResult:
    if shortcut.is_standalone and shortcut.trigger_vk not in SUPPORTED_MODIFIER_VKS:
        raise ShortcutError("A standalone modifier must identify its side.")
    families = _shortcut_families(shortcut)
    signature = (families, shortcut.trigger_vk)
    if signature in SECURE_SHORTCUTS:
        raise ShortcutError("Windows secure shortcuts cannot be intercepted.")
    warnings = (SYSTEM_WARNING,) if signature in SYSTEM_SHORTCUTS else ()
    return ValidationResult(shortcut, warnings)


def _split_tokens(raw: str) -> list[str]:
    pieces = raw.split("+")
    tokens = [token.strip() for token in pieces]
    if not tokens or not any(tokens):
        raise ShortcutError("Enter a shortcut.")
    if any(not token for token in tokens):
        raise ShortcutError("A shortcut cannot contain an empty key.")
    folded = [token.casefold() for token in tokens]
    if len(folded) != len(set(folded)):
        raise ShortcutError("A key may appear only once.")
    return folded


def parse_shortcut(raw: str) -> Shortcut:
    tokens = _split_tokens(raw)
    generic_modifiers = [MODIFIER_NAMES[token] for token in tokens if token in MODIFIER_NAMES]
    specific_vks = [SPECIFIC_MODIFIER_NAMES[token] for token in tokens if token in SPECIFIC_MODIFIER_NAMES]
    modifier_tokens = set(MODIFIER_NAMES) | set(SPECIFIC_MODIFIER_NAMES)
    trigger_tokens = [token for token in tokens if token not in modifier_tokens]

    if not trigger_tokens:
        if len(tokens) == 1 and specific_vks:
            vk = specific_vks[0]
            return validate_shortcut(
                Shortcut(frozenset(), vk, MODIFIER_VK_NAMES[vk], activation_kind=ActivationKind.RELEASE)
            ).shortcut
        if len(tokens) == 1 and generic_modifiers:
            raise ShortcutError("A standalone modifier must identify its side.")
        raise ShortcutError("Use one trigger key, or tap a single modifier.")
    if len(trigger_tokens) != 1:
        raise ShortcutError("Use modifier keys plus exactly one trigger key.")

    exact_modifiers = frozenset(specific_vks)
    for family in MODIFIER_ORDER:
        if family in generic_modifiers and exact_modifiers & MODIFIER_VKS[family]:
            raise ShortcutError("Generic and side-specific modifiers are ambiguous.")

    token = trigger_tokens[0]
    try:
        trigger_name = TOKEN_TO_NAME[token]
    except KeyError as exc:
        raise ShortcutError(f"Unsupported key: {token}") from exc
    shortcut = Shortcut(
        frozenset(generic_modifiers),
        VK_NAMES[trigger_name],
        trigger_name,
        exact_modifiers=exact_modifiers,
    )
    return validate_shortcut(shortcut).shortcut
