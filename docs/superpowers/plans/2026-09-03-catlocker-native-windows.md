# CatLocker Native Windows Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a reliable Windows 11 tray utility that toggles keyboard-only suppression through one native `WH_KEYBOARD_LL` hook while preserving F24, exact dual-control recovery, and mouse-accessible recovery.

**Architecture:** Keep shortcut parsing and input transitions pure and exhaustively tested, then place a thin `ctypes` hook adapter around that state machine. Serialize all authoritative state changes on the hook thread; let the native tray and hidden Tk settings UI communicate through posted messages and queues.

**Tech Stack:** Python 3.11+, standard library (`ctypes`, `tkinter`, `tomllib`, `winreg`, `threading`, `queue`), pytest for tests, PyInstaller and Inno Setup for packaging.

**Repository root:** `D:\Repositories\catlocker`

**Design specification:** `docs/superpowers/specs/2026-09-03-catlocker-native-windows-design.md`

---

## File Map

- Create `hotkeys.py`: VK catalog, shortcut parsing/validation/formatting, and the pure key transition state machine.
- Create `keyboard_hook.py`: `ctypes` Win32 declarations, hook thread/message loop, callback, commands, and lifecycle.
- Create `controller.py`: public command/acknowledgement API and state/error subscriptions.
- Replace `settings.py`: installed/portable config, atomic TOML persistence, and HKCU Run-key helpers.
- Create `tray.py`: native notification icon, popup menu, balloon notification, and Explorer restart handling.
- Create `settings_window.py`: hidden-root Tk settings and shortcut recorder.
- Replace `main.py`: composition, UI queue pump, readiness, fail-open error reporting, and shutdown.
- Delete `core.py`: remove legacy `pynput` keyboard/mouse listeners and global state.
- Delete `format.py`: remove the self-installing formatter helper.
- Create `tests/test_hotkeys.py`: parsing, validation, and matching tests.
- Create `tests/test_input_state.py`: transition, autorepeat, recovery, and key-disposition tests.
- Create `tests/test_keyboard_hook.py`: Win32 structure, translation, callback, and lifecycle adapter tests.
- Create `tests/test_controller.py`: commands, acknowledgements, timeouts, poisoning, and shutdown tests.
- Create `tests/test_settings.py`: path, config, atomicity, and registry tests.
- Create `tests/test_tray.py`: native menu/state behavior through an injected Win32 API seam.
- Create `tests/test_settings_window.py`: recorder normalization and transactional view-model tests without displaying a live window.
- Create `tests/test_main.py`: startup/shutdown ordering and failure-path tests.
- Create `tests/test_packaging.py`: dependency, identity, frozen-resource, and legacy-file assertions.
- Create `requirements-dev.txt`: pinned-enough test/build tooling only; runtime stays standard-library-only.
- Modify `requirements.txt`: remove `pynput` and `six`; document that runtime has no PyPI dependencies.
- Modify `build.py`, `build.bat`: rename artifacts and package CatLocker modules/assets.
- Delete `build.sh`, `keylock.iss`; create `catlocker.iss` for the Windows-only installer.
- Modify `README.md`: document CatLocker operation, recovery, limitations, local data, build, and manual verification.
- Create `docs/windows-manual-test-checklist.md`: executable Windows acceptance matrix and recorded environment/results.
- Preserve `LICENSE` unchanged.

## Chunk 1: Shortcut Domain

### Task 0: Bootstrap deterministic test tooling

**Files:**
- Create: `requirements-dev.txt`

- [ ] **Step 1: Record the development-only tools**

Create `requirements-dev.txt`:

```text
pytest==8.4.2
pyinstaller==6.15.0
```

Runtime dependencies do not belong in this file; `requirements.txt` is cleaned up in the packaging chunk.

- [ ] **Step 2: Install and verify pytest**

Run: `py -m pip install -r requirements-dev.txt`

Expected: installation succeeds.

Run: `py -m pytest --version`

Expected: output starts with `pytest 8.4.2`.

- [ ] **Step 3: Commit the development tooling declaration**

```powershell
git add -- requirements-dev.txt
git commit -m "build: declare CatLocker development tools"
```

### Task 1: Add the VK catalog and canonical shortcut parser

**Files:**
- Create: `hotkeys.py`
- Create: `tests/test_hotkeys.py`

- [ ] **Step 1: Write failing parser/model tests**

Create `tests/test_hotkeys.py` with focused examples:

```python
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
```

- [ ] **Step 2: Run the tests and verify the expected RED state**

Run: `py -m pytest tests/test_hotkeys.py -v`

Expected: collection fails with `ModuleNotFoundError: No module named 'hotkeys'`.

- [ ] **Step 3: Implement the minimal immutable model and VK catalog**

Create `hotkeys.py` with:

```python
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
```

- [ ] **Step 4: Run the parser tests and verify GREEN**

Run: `py -m pytest tests/test_hotkeys.py -v`

Expected: all Task 1 tests pass.

- [ ] **Step 5: Commit the parser/model slice**

```powershell
git add -- hotkeys.py tests/test_hotkeys.py
git commit -m "feat: add canonical Windows shortcut parser"
```

### Task 2: Add safety validation and modifier matching

**Files:**
- Modify: `hotkeys.py`
- Modify: `tests/test_hotkeys.py`

- [ ] **Step 1: Write failing validation and matching tests**

Append tests that assert:

```python
from hotkeys import (
    Modifier,
    VK_LCONTROL,
    VK_LMENU,
    VK_LSHIFT,
    VK_LWIN,
    VK_RCONTROL,
    active_modifier_families,
    validate_shortcut,
)


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
```

- [ ] **Step 2: Run the new tests and verify RED**

Run: `py -m pytest tests/test_hotkeys.py -v`

Expected: test collection fails with `ImportError: cannot import name 'VK_LCONTROL' from 'hotkeys'` because the modifier API does not exist yet.

- [ ] **Step 3: Implement VK modifier families and validation results**

Add exact modifier constants and families:

```python
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


@dataclass(frozen=True, slots=True)
class ValidationResult:
    shortcut: Shortcut
    warnings: tuple[str, ...] = ()


def active_modifier_families(pressed: set[int]) -> frozenset[Modifier]:
    return frozenset(family for family, vks in MODIFIER_VKS.items() if pressed & vks)
```

Add this method to the `Shortcut` class; the global helper is resolved when the method is called:

```python
def matches(self, pressed: set[int], trigger_vk: int) -> bool:
    return (
        trigger_vk == self.trigger_vk
        and active_modifier_families(pressed) == self.modifiers
    )
```

Keep emergency protection structural: modifier-only shortcuts remain invalid, and exact left/right control tokens are not accepted as configurable trigger names.

Use this complete validator shape:

```python
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
```

- [ ] **Step 4: Run all shortcut tests and verify GREEN**

Run: `py -m pytest tests/test_hotkeys.py -v`

Expected: all shortcut tests pass with no warnings or errors from pytest.

- [ ] **Step 5: Commit validation and matching**

```powershell
git add -- hotkeys.py tests/test_hotkeys.py
git commit -m "feat: validate CatLocker toggle shortcuts"
```

## Chunk 2: Pure Input State Machine

### Task 3: Implement true-toggle and emergency transition semantics

**Files:**
- Modify: `hotkeys.py`
- Create: `tests/test_input_state.py`

- [ ] **Step 1: Write failing transition tests**

Create `tests/test_input_state.py`:

```python
from hotkeys import (
    InputState,
    KeyEvent,
    Transition,
    VK_LCONTROL,
    VK_RCONTROL,
    parse_shortcut,
)

F24 = 0x87
K = 0x4B
LSHIFT = 0xA0


def down(vk, *, injected=False):
    return KeyEvent(vk=vk, is_keydown=True, injected=injected)


def up(vk, *, injected=False):
    return KeyEvent(vk=vk, is_keydown=False, injected=injected)


def test_f24_is_a_true_toggle_and_never_leaks():
    state = InputState(parse_shortcut("F24"))
    first = state.handle(down(F24))
    assert first == Transition(suppress=True, locked=True, changed=True, reason="toggle")
    assert state.handle(up(F24)).suppress is True
    second = state.handle(down(F24))
    assert second == Transition(suppress=True, locked=False, changed=True, reason="toggle")


def test_held_toggle_activates_only_once_until_release():
    state = InputState(parse_shortcut("F24"))
    assert state.handle(down(F24)).changed is True
    repeat = state.handle(down(F24))
    assert repeat.changed is False
    assert repeat.locked is True
    state.handle(up(F24))
    assert state.handle(down(F24)).changed is True


def test_exact_dual_control_always_unlocks_and_never_locks():
    state = InputState(parse_shortcut("F24"), locked=True)
    state.handle(down(VK_LCONTROL))
    result = state.handle(down(VK_RCONTROL))
    assert result == Transition(suppress=True, locked=False, changed=True, reason="emergency")
    state.handle(up(VK_RCONTROL))
    state.handle(up(VK_LCONTROL))
    state.set_locked(False)
    state.handle(down(VK_RCONTROL))
    result = state.handle(down(VK_LCONTROL))
    assert result.locked is False
    assert result.reason == "emergency"


def test_one_control_key_is_not_the_emergency_chord():
    state = InputState(parse_shortcut("F24"), locked=True)
    result = state.handle(down(VK_LCONTROL))
    assert result.changed is False
    assert result.suppress is True


def test_emergency_fires_once_until_either_control_is_released():
    state = InputState(parse_shortcut("F24"), locked=True)
    state.handle(down(VK_LCONTROL))
    assert state.handle(down(VK_RCONTROL)).reason == "emergency"
    state.set_locked(True)
    repeat = state.handle(down(VK_RCONTROL))
    assert repeat.changed is False
    assert repeat.locked is True
    state.handle(up(VK_RCONTROL))
    assert state.handle(down(VK_RCONTROL)).reason == "emergency"


def test_injected_f24_uses_the_normal_toggle_path():
    state = InputState(parse_shortcut("F24"))
    assert state.handle(down(F24, injected=True)).reason == "toggle"
```

- [ ] **Step 2: Run the transition tests and verify RED**

Run: `py -m pytest tests/test_input_state.py -v`

Expected: collection fails with `ImportError: cannot import name 'InputState' from 'hotkeys'`.

- [ ] **Step 3: Add event/result values and the minimal transition engine**

Add immutable values:

```python
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
```

Implement `InputState` with these owned fields:

```python
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

    def set_locked(self, locked: bool) -> Transition:
        changed = self.locked != locked
        self.locked = locked
        if locked:
            self.recording = False
        return Transition(False, self.locked, changed, "command" if changed else None)

    def handle(self, event: KeyEvent) -> Transition:
        return self._handle_down(event) if event.is_keydown else self._handle_up(event)
```

Implement the first minimal `_handle_down` in this exact order:

1. If `event.vk` is already in `pressed`, it is autorepeat: suppress according to the current `locked` state and return with no transition. Task 4 deliberately replaces this preliminary rule with original-press disposition handling.
2. Add the VK to `pressed`.
3. If exact left and right control are now down and emergency is not latched, latch emergency, set `locked=False`, and force suppression of the completing VK.
4. Else, if not recording, the toggle is not latched, and `shortcut.matches(pressed, event.vk)`, latch the toggle, invert `locked`, and force suppression of the trigger.
5. Else suppress according to the resulting `locked` value.
6. Put this first keydown in exactly one of `passed_down` or `suppressed_down`; Task 4 will begin relying on these sets for releases/repeats.
7. Return `Transition` with `changed` based on the previous `locked` value and reason `emergency`, `toggle`, or `None`.

Implement the preliminary `_handle_up` by suppressing according to the current `locked` state, then removing the VK from `pressed` and both disposition sets. Clear `toggle_latched` when the configured trigger is released. Clear `emergency_latched` when either exact control is released. Return no state change. Original first-keydown disposition is the next failing-test slice.

- [ ] **Step 4: Run transition and parser tests and verify GREEN**

Run: `py -m pytest tests/test_input_state.py tests/test_hotkeys.py -v`

Expected: all tests pass.

- [ ] **Step 5: Commit the transition engine**

```powershell
git add -- hotkeys.py tests/test_input_state.py
git commit -m "feat: add CatLocker keyboard state machine"
```

### Task 4: Harden down/up disposition, recording, and shortcut replacement

**Files:**
- Modify: `hotkeys.py`
- Modify: `tests/test_input_state.py`

- [ ] **Step 1: Write failing cross-transition disposition tests**

Append:

```python
def test_passed_modifier_releases_after_shortcut_locks():
    state = InputState(parse_shortcut("Ctrl+Shift+K"))
    assert state.handle(down(VK_LCONTROL)).suppress is False
    assert state.handle(down(LSHIFT)).suppress is False
    assert state.handle(down(K)).reason == "toggle"
    assert state.handle(up(K)).suppress is True
    assert state.handle(up(LSHIFT)).suppress is False
    assert state.handle(up(VK_LCONTROL)).suppress is False


def test_suppressed_chord_releases_after_shortcut_unlocks():
    state = InputState(parse_shortcut("Ctrl+Shift+K"), locked=True)
    assert state.handle(down(VK_LCONTROL)).suppress is True
    assert state.handle(down(LSHIFT)).suppress is True
    assert state.handle(down(K)).reason == "toggle"
    assert state.locked is False
    assert state.handle(up(K)).suppress is True
    assert state.handle(up(LSHIFT)).suppress is True
    assert state.handle(up(VK_LCONTROL)).suppress is True


def test_passed_key_repeat_is_blocked_after_lock_but_release_passes():
    state = InputState(parse_shortcut("F24"))
    assert state.handle(down(K)).suppress is False
    state.set_locked(True)
    assert state.handle(down(K)).suppress is True
    assert state.handle(up(K)).suppress is False


def test_suppressed_key_repeat_stays_blocked_after_unlock():
    state = InputState(parse_shortcut("F24"), locked=True)
    assert state.handle(down(K)).suppress is True
    state.set_locked(False)
    assert state.handle(down(K)).suppress is True
    assert state.handle(up(K)).suppress is True


def test_unrelated_keys_do_not_prevent_toggle_but_extra_modifiers_do():
    state = InputState(parse_shortcut("Ctrl+Shift+K"))
    state.handle(down(0x41))
    state.handle(down(VK_LCONTROL))
    state.handle(down(LSHIFT))
    assert state.handle(down(K)).reason == "toggle"

    state = InputState(parse_shortcut("Ctrl+Shift+K"))
    state.handle(down(VK_LCONTROL))
    state.handle(down(LSHIFT))
    state.handle(down(0xA4))
    assert state.handle(down(K)).reason is None
```

- [ ] **Step 2: Run the focused tests and verify RED where behavior is missing**

Run: `py -m pytest tests/test_input_state.py -v`

Expected: at least the first cross-transition or autorepeat assertion fails until exclusive disposition logic is complete.

- [ ] **Step 3: Complete exclusive disposition behavior**

Refactor only as needed so:

```python
def _repeat_is_suppressed(self, vk: int) -> bool:
    return vk not in self.passed_down or self.locked

def _release_is_suppressed(self, vk: int) -> bool:
    if vk in self.suppressed_down:
        return True
    if vk in self.passed_down:
        return False
    return self.locked
```

Assert internally during tests that `passed_down.isdisjoint(suppressed_down)` after every event. Do not clear either set merely because lock state changes.

- [ ] **Step 4: Verify the disposition slice is GREEN**

Run: `py -m pytest tests/test_input_state.py -v`

Expected: all current transition/disposition tests pass.

- [ ] **Step 5: Add failing recording and replacement tests**

Append:

```python
def test_recording_suspends_toggle_matching_but_not_emergency():
    state = InputState(parse_shortcut("F24"))
    assert state.enter_recording() is True
    result = state.handle(down(F24))
    assert result.reason is None
    assert result.suppress is False
    state.handle(up(F24))
    state.handle(down(VK_LCONTROL))
    assert state.handle(down(VK_RCONTROL)).reason == "emergency"
    state.handle(up(VK_RCONTROL))
    state.handle(up(VK_LCONTROL))
    state.set_locked(True)
    assert state.recording is False


def test_recording_cannot_start_locked():
    state = InputState(parse_shortcut("F24"), locked=True)
    assert state.enter_recording() is False


def test_new_trigger_already_held_requires_release_before_toggle():
    state = InputState(parse_shortcut("F24"))
    state.handle(down(K))
    assert state.replace_shortcut(parse_shortcut("K")) is True
    assert state.handle(down(K)).reason is None
    state.handle(up(K))
    assert state.handle(down(K)).reason == "toggle"


def test_releasing_old_trigger_after_replacement_does_not_toggle():
    state = InputState(parse_shortcut("F24"), locked=True)
    state.handle(down(F24))
    assert state.locked is False
    assert state.replace_shortcut(parse_shortcut("K")) is True
    assert state.handle(up(F24)).changed is False


def test_shortcut_replacement_is_rejected_while_locked():
    state = InputState(parse_shortcut("F24"), locked=True)
    assert state.replace_shortcut(parse_shortcut("K")) is False
    assert state.shortcut.canonical == "F24"


def test_replacement_exits_recording_mode():
    state = InputState(parse_shortcut("F24"))
    state.enter_recording()
    assert state.replace_shortcut(parse_shortcut("K")) is True
    assert state.recording is False


def test_untracked_late_keyup_follows_current_lock_state():
    assert InputState(parse_shortcut("F24")).handle(up(K)).suppress is False
    assert InputState(parse_shortcut("F24"), locked=True).handle(up(K)).suppress is True
```

- [ ] **Step 6: Run the new tests and verify RED**

Run: `py -m pytest tests/test_input_state.py -k "recording or trigger or replacement" -v`

Expected: failures because `enter_recording` and `replace_shortcut` do not exist.

- [ ] **Step 7: Implement recording and safe replacement**

Add:

```python
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
    self.recording = False
    self.shortcut = shortcut
    self.toggle_latched = shortcut.trigger_vk in self.pressed
    return True
```

Ensure `set_locked(True)` and every external toggle request exit recording before changing state. Existing pressed/disposition sets must remain untouched.

- [ ] **Step 8: Add deterministic cat-mashing invariant coverage**

Add a seeded test that constructs a fixed sequence of at least 100 down/repeat/up events across letters, both controls, both shifts, F24, and media VKs, interspersed with `set_locked()` calls. After each event assert:

```python
assert state.passed_down.isdisjoint(state.suppressed_down)
assert state.passed_down | state.suppressed_down == state.pressed
```

Finish by releasing every remaining VK and assert all three sets are empty. Keep the sequence deterministic in source or use `random.Random(20260903)`.

- [ ] **Step 9: Run the complete pure-domain suite and verify GREEN**

Run: `py -m pytest tests/test_hotkeys.py tests/test_input_state.py -v`

Expected: all tests pass with no warnings.

- [ ] **Step 10: Commit the hardened state machine**

```powershell
git add -- hotkeys.py tests/test_input_state.py
git commit -m "test: harden CatLocker key transition invariants"
```

## Chunk 3: Win32 Hook and Controller

### Task 5: Define pointer-safe Win32 keyboard event translation

**Files:**
- Create: `keyboard_hook.py`
- Create: `tests/test_keyboard_hook.py`

- [ ] **Step 1: Write failing constants, structure, and translation tests**

Create `tests/test_keyboard_hook.py`:

```python
import ctypes

import pytest

from keyboard_hook import (
    HC_ACTION,
    HOOKPROC,
    KBDLLHOOKSTRUCT,
    LLKHF_INJECTED,
    WM_KEYDOWN,
    WM_KEYUP,
    WM_SYSKEYDOWN,
    WM_SYSKEYUP,
    event_from_message,
)


@pytest.mark.parametrize(
    ("message", "is_keydown"),
    [
        (WM_KEYDOWN, True),
        (WM_SYSKEYDOWN, True),
        (WM_KEYUP, False),
        (WM_SYSKEYUP, False),
    ],
)
def test_keyboard_messages_translate_to_pure_events(message, is_keydown):
    data = KBDLLHOOKSTRUCT(vkCode=0x87, scanCode=0, flags=LLKHF_INJECTED, time=0, dwExtraInfo=0)
    event = event_from_message(message, data)
    assert event.vk == 0x87
    assert event.is_keydown is is_keydown
    assert event.injected is True


def test_unknown_message_is_not_a_keyboard_event():
    assert event_from_message(0x9999, KBDLLHOOKSTRUCT()) is None


def test_kbd_structure_uses_pointer_sized_extra_info():
    field_type = dict(KBDLLHOOKSTRUCT._fields_)["dwExtraInfo"]
    assert ctypes.sizeof(field_type) == ctypes.sizeof(ctypes.c_void_p)


def test_hook_callback_uses_pointer_sized_windows_types():
    assert ctypes.sizeof(HOOKPROC._argtypes_[1]) == ctypes.sizeof(ctypes.c_void_p)
    assert ctypes.sizeof(HOOKPROC._argtypes_[2]) == ctypes.sizeof(ctypes.c_void_p)
    assert ctypes.sizeof(HOOKPROC._restype_) == ctypes.sizeof(ctypes.c_void_p)
```

- [ ] **Step 2: Run adapter tests and verify RED**

Run: `py -m pytest tests/test_keyboard_hook.py -v`

Expected: collection fails with `ModuleNotFoundError: No module named 'keyboard_hook'`.

- [ ] **Step 3: Implement declarations and pure translation**

Create `keyboard_hook.py` with constants `WH_KEYBOARD_LL=13`, `HC_ACTION=0`, the four keyboard messages, `WM_QUIT`, `WM_APP_COMMAND=0x8001`, `PM_NOREMOVE=0x0000`, and `LLKHF_INJECTED=0x10`. Define:

```python
import ctypes
from ctypes import wintypes

from hotkeys import KeyEvent


ULONG_PTR = ctypes.c_size_t
LRESULT = ctypes.c_ssize_t


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


def event_from_message(message: int, data: KBDLLHOOKSTRUCT) -> KeyEvent | None:
    if message in (WM_KEYDOWN, WM_SYSKEYDOWN):
        is_keydown = True
    elif message in (WM_KEYUP, WM_SYSKEYUP):
        is_keydown = False
    else:
        return None
    return KeyEvent(int(data.vkCode), is_keydown, bool(data.flags & LLKHF_INJECTED))
```

Define the exact callback and handle aliases:

```python
HHOOK = ctypes.c_void_p
HINSTANCE = ctypes.c_void_p
HOOKPROC = ctypes.WINFUNCTYPE(
    LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM
)
```

Bind default Windows APIs lazily inside `Win32Api.__init__`, not at module import, so pure tests can import safely. Load both DLLs with `ctypes.WinDLL(name, use_last_error=True)`. Use these exact declarations:

```python
user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, HINSTANCE, wintypes.DWORD]
user32.SetWindowsHookExW.restype = HHOOK
user32.CallNextHookEx.argtypes = [HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
user32.CallNextHookEx.restype = LRESULT
user32.UnhookWindowsHookEx.argtypes = [HHOOK]
user32.UnhookWindowsHookEx.restype = wintypes.BOOL
user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
user32.GetMessageW.restype = wintypes.BOOL
user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.TranslateMessage.restype = wintypes.BOOL
user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.DispatchMessageW.restype = LRESULT
user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.PostThreadMessageW.restype = wintypes.BOOL
user32.PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT, wintypes.UINT]
user32.PeekMessageW.restype = wintypes.BOOL
kernel32.GetCurrentThreadId.argtypes = []
kernel32.GetCurrentThreadId.restype = wintypes.DWORD
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
kernel32.GetModuleHandleW.restype = HINSTANCE
user32.PostQuitMessage.argtypes = [ctypes.c_int]
user32.PostQuitMessage.restype = None
```

`Win32Api.install_hook(callback)` obtains `module_handle = GetModuleHandleW(None)` and calls `SetWindowsHookExW(WH_KEYBOARD_LL, callback, module_handle, 0)`. A null return raises `ctypes.WinError(ctypes.get_last_error())`.

- [ ] **Step 4: Run adapter and domain tests and verify GREEN**

Run: `py -m pytest tests/test_keyboard_hook.py tests/test_input_state.py tests/test_hotkeys.py -v`

Expected: all tests pass.

- [ ] **Step 5: Commit the Win32 declaration slice**

```powershell
git add -- keyboard_hook.py tests/test_keyboard_hook.py
git commit -m "feat: translate low-level Windows keyboard events"
```

### Task 6: Add the dedicated hook thread, callback, and command protocol

**Files:**
- Modify: `keyboard_hook.py`
- Modify: `tests/test_keyboard_hook.py`

- [ ] **Step 1: Write a deterministic fake Win32 API**

In `tests/test_keyboard_hook.py`, add `FakeWin32Api` with:

- fixed Win32 `thread_id=42`, `hook_handle=1234`, and `call_next_return=77`;
- recorded calls for install, call-next, unhook, post-thread-message, and post-quit;
- `install_threads` and `unhook_threads` recording `threading.get_ident()`;
- a `queue.Queue` message loop consumed by `get_message()`;
- `initialize_message_queue()` recording that startup created the owner-thread queue;
- `fail_next_get_message(error)` causing the next `get_message()` call to raise that error, simulating native `GetMessageW == -1`;
- `post_thread_message()` enqueuing `(message, wparam, lparam)`;
- `install_hook(callback)` retaining the callback and returning the handle;
- `call_next(...)` recording its arguments and returning `call_next_return`;
- `emit(n_code, message, structure)` invoking the retained callback as `callback(n_code, message, ctypes.addressof(structure))`;
- `get_message()` returning the next queued message and returning `0` for `WM_QUIT`.

Keep this fake in tests; production code receives the API through constructor injection.

- [ ] **Step 2: Write failing hook lifecycle and suppression tests**

Add:

```python
from keyboard_hook import CommandKind, EngineEvent, HookStopped, KeyboardHook, WM_APP_COMMAND
from hotkeys import parse_shortcut


def test_hook_installs_on_own_thread_and_unhooks_on_stop():
    caller_ident = threading.get_ident()
    api = FakeWin32Api()
    hook = KeyboardHook(parse_shortcut("F24"), api=api)
    hook.start(timeout=1)
    assert api.install_calls == 1
    assert hook.thread_id == 42
    hook.stop(timeout=1)
    assert api.unhooked == [1234]
    assert api.install_threads == api.unhook_threads
    assert api.install_threads[0] != caller_ident
    assert not hook.is_alive()


def test_callback_suppresses_locked_key_and_passes_unlocked_key():
    api = FakeWin32Api()
    hook = KeyboardHook(parse_shortcut("F24"), api=api)
    hook.start(timeout=1)
    assert api.emit(HC_ACTION, WM_KEYDOWN, KBDLLHOOKSTRUCT(vkCode=0x41)) == api.call_next_return
    api.emit(HC_ACTION, WM_KEYDOWN, KBDLLHOOKSTRUCT(vkCode=0x87))
    api.emit(HC_ACTION, WM_KEYUP, KBDLLHOOKSTRUCT(vkCode=0x87))
    assert api.emit(HC_ACTION, WM_KEYDOWN, KBDLLHOOKSTRUCT(vkCode=0x42)) == 1
    hook.stop(timeout=1)


def test_negative_hook_code_always_calls_next():
    api = FakeWin32Api()
    hook = KeyboardHook(parse_shortcut("F24"), api=api)
    hook.start(timeout=1)
    api.emit(-1, WM_KEYDOWN, KBDLLHOOKSTRUCT(vkCode=0x87))
    assert api.call_next_calls[-1][0] == -1
    hook.stop(timeout=1)


def test_installation_exception_is_reported_without_start_timeout():
    api = FakeWin32Api(install_error=OSError("install failed"))
    hook = KeyboardHook(parse_shortcut("F24"), api=api)
    with pytest.raises(OSError, match="install failed"):
        hook.start(timeout=1)
    assert hook.ready.is_set()
```

- [ ] **Step 3: Run lifecycle tests and verify RED**

Run: `py -m pytest tests/test_keyboard_hook.py -k "hook or callback" -v`

Expected: collection fails because `KeyboardHook` and `CommandKind` are missing.

- [ ] **Step 4: Implement the hook thread and fail-open callback**

Add immutable command/result types:

```python
class CommandKind(Enum):
    SET_LOCKED = auto()
    TOGGLE = auto()
    REPLACE_SHORTCUT = auto()
    ENTER_RECORDING = auto()
    EXIT_RECORDING = auto()
    FAIL_OPEN = auto()
    STOP = auto()

@dataclass(frozen=True, slots=True)
class HookCommand:
    command_id: int
    kind: CommandKind
    payload: object = None
    reply: queue.SimpleQueue | None = None

@dataclass(frozen=True, slots=True)
class CommandResult:
    command_id: int
    accepted: bool
    locked: bool
    shortcut_generation: int

@dataclass(frozen=True, slots=True)
class EngineEvent:
    kind: str
    locked: bool
    reason: str | None = None
    error: BaseException | None = None
```

`KeyboardHook` owns `InputState`, `threading.Thread`, `threading.Event` values for readiness and terminal fail-open, a producer-lock-protected `dict[int, HookCommand]` of pending commands, an unbounded `queue.SimpleQueue[EngineEvent]`, the thread ID, hook handle, callback reference, installation exception, and shortcut generation.

`start(timeout)` starts a daemon thread (normal shutdown still joins it), waits a bounded time for readiness, and raises the captured installation error. `_run()` records the Win32 thread ID, calls `api.initialize_message_queue()` (the real adapter wraps `PeekMessageW(..., PM_NOREMOVE)`) to force creation of that thread's message queue, creates and retains the callback, then attempts installation. Its `except BaseException` stores the installation error; its `finally` always sets readiness if startup did not reach the message loop. Successful installation sets readiness only after the queue exists and pumps messages. `WM_APP_COMMAND` uses `wParam` as the command ID and pops exactly that command from the pending dictionary. `STOP` first forces unlocked, unhooks on the owner thread, replies, and exits the message loop. A final cleanup block best-effort unhooks any remaining handle and reports unlocked.

Treat `GetMessageW` results explicitly: greater than zero dispatches, zero exits, and `-1` raises `ctypes.WinError`. A message-loop error sets terminal fail-open, publishes a fatal event, and reaches the owner-thread unhook cleanup without examining stale `MSG` contents.

The callback must follow:

```python
def _callback(self, n_code, w_param, l_param):
    if n_code < HC_ACTION or self.fail_open.is_set():
        return self.api.call_next(self.hook_handle, n_code, w_param, l_param)
    try:
        data = ctypes.cast(l_param, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
        event = event_from_message(int(w_param), data)
        if event is None:
            return self.api.call_next(self.hook_handle, n_code, w_param, l_param)
        transition = self.state.handle(event)
        if transition.changed:
            self.events.put(EngineEvent("state", transition.locked, transition.reason))
        if transition.suppress:
            return 1
        return self.api.call_next(self.hook_handle, n_code, w_param, l_param)
    except BaseException as exc:
        self.fail_open.set()
        self.state.set_locked(False)
        self.events.put(EngineEvent("fatal", False, "callback", exc))
        return self.api.call_next(self.hook_handle, n_code, w_param, l_param)
```

- [ ] **Step 5: Run lifecycle tests and verify GREEN**

Run: `py -m pytest tests/test_keyboard_hook.py -v`

Expected: all current adapter/lifecycle tests pass.

- [ ] **Step 6: Write failing command and poisoned-engine tests**

Add tests that submit each command and assert:

```python
def test_commands_are_serialized_on_hook_thread():
    api = FakeWin32Api()
    hook = started_hook(api, "F24")
    assert hook.submit(CommandKind.SET_LOCKED, True, timeout=1).locked is True
    assert hook.submit(CommandKind.SET_LOCKED, False, timeout=1).locked is False
    assert hook.submit(CommandKind.ENTER_RECORDING, timeout=1).accepted is True
    assert hook.submit(CommandKind.REPLACE_SHORTCUT, parse_shortcut("K"), timeout=1).accepted is True
    assert hook.state.recording is False
    hook.stop(timeout=1)


def test_callback_exception_poisoning_passes_all_later_events(monkeypatch):
    api = FakeWin32Api()
    hook = started_hook(api, "F24")
    monkeypatch.setattr(hook.state, "handle", lambda event: (_ for _ in ()).throw(RuntimeError("boom")))
    assert api.emit(HC_ACTION, WM_KEYDOWN, KBDLLHOOKSTRUCT(vkCode=0x41)) == api.call_next_return
    before = len(api.call_next_calls)
    assert api.emit(HC_ACTION, WM_KEYDOWN, KBDLLHOOKSTRUCT(vkCode=0x42)) == api.call_next_return
    assert len(api.call_next_calls) == before + 1
    assert hook.fail_open.is_set()
    assert hook.events.get_nowait().kind == "fatal"
    hook.stop(timeout=1)


def test_fail_open_irreversibly_rejects_later_state_changes():
    api = FakeWin32Api()
    hook = started_hook(api, "F24")
    hook.enter_fail_open()
    with pytest.raises(HookStopped):
        hook.submit(CommandKind.SET_LOCKED, True, timeout=1)
    with pytest.raises(HookStopped):
        hook.submit(CommandKind.REPLACE_SHORTCUT, parse_shortcut("K"), timeout=1)
    assert hook.locked is False
    hook.stop(timeout=1)
```

Also add `test_message_loop_error_enters_fail_open_cleanup` by calling `api.fail_next_get_message(OSError("GetMessageW failed"))`, waking the loop, and asserting fail-open, fatal event, and owner-thread unhook. Test failed hook installation, recording rejection while locked, external lock/toggle exiting recording, shortcut generation increments only for accepted replacement, and mismatched/late command IDs being ignored by the submitter.

- [ ] **Step 7: Run command tests and verify RED**

Run: `py -m pytest tests/test_keyboard_hook.py -k "command or poison or fail_open or recording or installation or message_loop" -v`

Expected: at least one failure because `submit()` and complete command dispatch are not implemented.

- [ ] **Step 8: Implement command submission and dispatch**

`submit()` allocates a monotonically increasing command ID and inserts the command in `_pending_commands` under the producer lock. It calls `PostThreadMessageW(thread_id, WM_APP_COMMAND, command_id, 0)`. If posting fails, it removes that exact pending entry before raising `HookStopped`, so no later wake-up can execute a command reported as failed. It then waits on only that command's reply queue outside the hook callback. Verify returned `command_id`; ignore any mismatch until the original bounded deadline. Raise `HookTimeout` on expiry.

`enter_fail_open()` is callable from any thread. It sets the terminal event immediately before best-effort posting `FAIL_OPEN`, so the callback and public `locked` property become fail-open even if the hook thread is stalled. Once the event is set, `submit()` rejects every new command except `FAIL_OPEN` and `STOP`.

Dispatch rules:

- `SET_LOCKED`: exit recording when locking, then call `state.set_locked(bool(payload))`.
- `TOGGLE`: exit recording, then call `state.set_locked(not state.locked)`.
- `REPLACE_SHORTCUT`: accept only through `state.replace_shortcut(payload)`; increment generation only when accepted.
- `ENTER_RECORDING`/`EXIT_RECORDING`: call the matching state methods.
- `FAIL_OPEN`: set terminal fail-open and force the reported state unlocked.
- `STOP`: terminal fail-open, force unlocked, unhook, acknowledge, then quit.

When fail-open is set, dispatch rejects any already-posted state-changing command and replies with `accepted=False, locked=False`; it handles only `FAIL_OPEN` and `STOP`. Publish state events only for actual changes. Put replies before handling the next command, but never wait for a reply consumer.

Expose `locked` as `False if fail_open.is_set() else state.locked`. This is the only hook state snapshot the controller may read.

- [ ] **Step 9: Run all hook/domain tests and verify GREEN**

Run: `py -m pytest tests/test_keyboard_hook.py tests/test_input_state.py tests/test_hotkeys.py -v`

Expected: all tests pass.

- [ ] **Step 10: Commit the hook engine**

```powershell
git add -- keyboard_hook.py tests/test_keyboard_hook.py
git commit -m "feat: add dedicated fail-open keyboard hook engine"
```

### Task 7: Add the controller and timeout policy

**Files:**
- Create: `controller.py`
- Create: `tests/test_controller.py`

- [ ] **Step 1: Write failing controller tests with a fake hook**

Create `tests/test_controller.py` with a `FakeHook` that records `(kind, payload, timeout)` and returns chosen `CommandResult` values. Cover:

```python
import pytest

from controller import CatModeController, EngineUnhealthy
from hotkeys import parse_shortcut
from keyboard_hook import CommandKind, CommandResult, HookStopped, HookTimeout


def test_controller_routes_all_lock_actions_to_same_engine():
    hook = FakeHook()
    controller = CatModeController(hook, command_timeout=0.25)
    controller.lock()
    controller.unlock()
    controller.toggle()
    assert [call.kind for call in hook.calls] == [
        CommandKind.SET_LOCKED,
        CommandKind.SET_LOCKED,
        CommandKind.TOGGLE,
    ]
    assert hook.calls[0].payload is True
    assert hook.calls[1].payload is False


def test_timeout_enters_terminal_fail_open_and_raises():
    hook = FakeHook(error=HookTimeout("stalled"))
    controller = CatModeController(hook, command_timeout=0.25)
    with pytest.raises(EngineUnhealthy):
        controller.lock()
    assert hook.fail_open_calls == 1


def test_post_failure_enters_terminal_fail_open_and_raises():
    hook = FakeHook(error=HookStopped("post failed"))
    controller = CatModeController(hook, command_timeout=0.25)
    with pytest.raises(EngineUnhealthy):
        controller.toggle()
    assert hook.fail_open_calls == 1


def test_replace_shortcut_returns_generation_and_rejects_locked_engine():
    hook = FakeHook(result=CommandResult(1, False, True, 4))
    controller = CatModeController(hook)
    assert controller.replace_shortcut(parse_shortcut("K")) is None


def test_accepted_replacement_returns_new_generation():
    hook = FakeHook(result=CommandResult(1, True, False, 5))
    controller = CatModeController(hook)
    assert controller.replace_shortcut(parse_shortcut("K")) == 5


def test_controller_exposes_explicit_terminal_fail_open():
    hook = FakeHook()
    controller = CatModeController(hook)
    controller.enter_fail_open()
    assert hook.fail_open_calls == 1
```

Add equivalent timeout tests for `replace_shortcut`, `enter_recording`, and `exit_recording`, plus an event-subscription test showing the controller reads the hook's authoritative `locked` snapshot rather than storing a second Boolean. Persistence rollback uses a second ordinary `replace_shortcut(old_shortcut)` call and is tested with the settings transaction in Chunk 5, Task 11.

- [ ] **Step 2: Run controller tests and verify RED**

Run: `py -m pytest tests/test_controller.py -v`

Expected: collection fails with `ModuleNotFoundError: No module named 'controller'`.

- [ ] **Step 3: Implement the thin controller**

Create `controller.py`. It must not store `locked`. Its property returns `hook.locked`; lock/unlock/toggle call `submit` with the specified timeout. `replace_shortcut` returns the accepted shortcut generation or `None`; `enter_recording` and `exit_recording` return Booleans. Expose `enter_fail_open()` as a narrow public wrapper over `hook.enter_fail_open()` for lifecycle/transactional fatal paths. `KeyboardHook.submit()` alone validates private command IDs. Any `HookTimeout` or post failure calls `enter_fail_open()` immediately and raises `EngineUnhealthy`, allowing the main lifecycle owner to shut down.

Expose the engine's `SimpleQueue[EngineEvent]` for the main thread to fan out to Tk and tray. Do not consume it on a controller worker thread.

- [ ] **Step 4: Run controller and hook tests and verify GREEN**

Run: `py -m pytest tests/test_controller.py tests/test_keyboard_hook.py -v`

Expected: all tests pass.

- [ ] **Step 5: Commit the controller boundary**

```powershell
git add -- controller.py tests/test_controller.py
git commit -m "feat: centralize CatLocker control commands"
```

## Chunk 4: Local Settings and Login Startup

### Task 8: Replace legacy settings with validated standard-library TOML storage

**Files:**
- Replace: `settings.py`
- Create: `tests/test_settings.py`

- [ ] **Step 1: Write failing path and load tests**

Create `tests/test_settings.py`:

```python
from pathlib import Path

import pytest

from hotkeys import ShortcutError
from settings import AppSettings, load_settings, resolve_config_path


def test_installed_mode_uses_local_appdata(tmp_path):
    exe_dir = tmp_path / "install"
    exe_dir.mkdir()
    path = resolve_config_path(exe_dir, tmp_path / "local")
    assert path == tmp_path / "local" / "CatLocker" / "config.toml"


def test_existing_beside_executable_file_enables_portable_mode(tmp_path):
    portable = tmp_path / "catlocker.toml"
    portable.write_text('toggle_hotkey = "F24"\nnotifications = true\n', encoding="utf-8")
    assert resolve_config_path(tmp_path, tmp_path / "local") == portable


def test_missing_config_is_created_with_safe_defaults(tmp_path):
    path = tmp_path / "config.toml"
    settings = load_settings(path)
    assert settings == AppSettings(toggle_hotkey="F24", notifications=True)
    assert path.exists()


def test_partial_config_merges_defaults_and_normalizes_hotkey(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('toggle_hotkey = "shift+ctrl+k"\n', encoding="utf-8")
    assert load_settings(path) == AppSettings("Ctrl+Shift+K", True)


def test_invalid_hotkey_falls_back_to_f24_without_losing_notification_choice(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('toggle_hotkey = "ctrl"\nnotifications = false\n', encoding="utf-8")
    assert load_settings(path) == AppSettings("F24", False)
```

- [ ] **Step 2: Run settings tests and verify RED**

Run: `py -m pytest tests/test_settings.py -v`

Expected: existing `settings.py` raises an import error because `AppSettings` and `resolve_config_path` do not exist.

- [ ] **Step 3: Implement settings model, path selection, and minimal TOML codec**

Replace `settings.py` with standard-library-only code. Define:

```python
@dataclass(frozen=True, slots=True)
class AppSettings:
    toggle_hotkey: str = "F24"
    notifications: bool = True


def resolve_config_path(executable_dir: Path, local_appdata: Path) -> Path:
    portable = executable_dir / "catlocker.toml"
    return portable if portable.is_file() else local_appdata / "CatLocker" / "config.toml"
```

`load_settings(path)` creates the parent directory and default file when missing. Read with `tomllib.load`, require the two root values to have the expected types, parse and validate the hotkey, and store its canonical form. A stored warning-only shortcut remains valid. Invalid individual values fall back independently to their defaults.

Implement only the TOML CatLocker writes:

```python
def encode_settings(settings: AppSettings) -> str:
    hotkey = settings.toggle_hotkey.replace("\\", "\\\\").replace('"', '\\"')
    notifications = "true" if settings.notifications else "false"
    return f'toggle_hotkey = "{hotkey}"\nnotifications = {notifications}\n'
```

- [ ] **Step 4: Run load/path tests and verify GREEN**

Run: `py -m pytest tests/test_settings.py -v`

Expected: current tests pass.

- [ ] **Step 5: Write failing corruption and atomic-save tests**

Append:

```python
from settings import save_settings


def test_corrupt_toml_is_preserved_before_defaults_are_written(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("not = [valid", encoding="utf-8")
    assert load_settings(path) == AppSettings()
    assert (tmp_path / "config.toml.corrupt").read_text(encoding="utf-8") == "not = [valid"
    assert load_settings(path) == AppSettings()


def test_invalid_utf8_is_preserved_as_corrupt_configuration(tmp_path):
    path = tmp_path / "config.toml"
    path.write_bytes(b"\xff\xfe")
    assert load_settings(path) == AppSettings()
    assert (tmp_path / "config.toml.corrupt").read_bytes() == b"\xff\xfe"


def test_corrupt_backup_uses_first_free_numbered_name(tmp_path):
    path = tmp_path / "config.toml"
    (tmp_path / "config.toml.corrupt").write_text("older", encoding="utf-8")
    path.write_text("not = [valid", encoding="utf-8")
    load_settings(path)
    assert (tmp_path / "config.toml.corrupt").read_text(encoding="utf-8") == "older"
    assert (tmp_path / "config.toml.corrupt.1").read_text(encoding="utf-8") == "not = [valid"
    assert load_settings(path) == AppSettings()


def test_atomic_save_round_trips_and_leaves_no_temp_file(tmp_path):
    path = tmp_path / "config.toml"
    save_settings(path, AppSettings("Ctrl+Alt+F12", False))
    assert load_settings(path) == AppSettings("Ctrl+Alt+F12", False)
    assert list(tmp_path.glob(".catlocker-*.tmp")) == []


def test_replace_failure_preserves_previous_file_and_cleans_temp(tmp_path):
    path = tmp_path / "config.toml"
    save_settings(path, AppSettings())
    original = path.read_bytes()

    def fail_replace(source, destination):
        raise OSError("disk full")

    with pytest.raises(OSError, match="disk full"):
        save_settings(path, AppSettings("K", False), replace=fail_replace)
    assert path.read_bytes() == original
    assert list(tmp_path.glob(".catlocker-*.tmp")) == []


def test_invalid_shortcut_never_touches_existing_file(tmp_path):
    path = tmp_path / "config.toml"
    save_settings(path, AppSettings())
    original = path.read_bytes()
    with pytest.raises(ShortcutError):
        save_settings(path, AppSettings("Ctrl", False))
    assert path.read_bytes() == original


def test_cleanup_failure_does_not_mask_replace_failure(tmp_path):
    path = tmp_path / "config.toml"
    save_settings(path, AppSettings())

    def fail_replace(source, destination):
        raise OSError("disk full")

    def fail_unlink(path):
        raise PermissionError("cleanup denied")

    with pytest.raises(OSError, match="disk full"):
        save_settings(
            path,
            AppSettings("K", False),
            replace=fail_replace,
            unlink=fail_unlink,
        )
```

- [ ] **Step 6: Run atomicity tests and verify RED**

Run: `py -m pytest tests/test_settings.py -k "corrupt or atomic or replace or invalid_shortcut or cleanup" -v`

Expected: failures because corrupt preservation and `save_settings` are missing.

- [ ] **Step 7: Implement corruption preservation and atomic replacement**

On `tomllib.TOMLDecodeError` or `UnicodeDecodeError`, move the unreadable bytes to `config.toml.corrupt`, or the first free `config.toml.corrupt.N`, before writing defaults. Do not classify permission/I/O errors as content corruption; propagate those errors.

`save_settings(path, settings, replace=os.replace, unlink=os.unlink)` must:

1. Validate and canonicalize the hotkey before touching disk.
2. Create the parent directory.
3. Open a same-directory `.catlocker-*.tmp` with `tempfile.NamedTemporaryFile(delete=False)` inside a `with` block.
4. Write UTF-8 with `newline="\n"`, flush, and `os.fsync` inside that block.
5. Exit the `with` block so the file is closed on Windows.
6. Call the injected `replace(temp_path, path)` only after close.
7. In `finally`, best-effort unlink a still-existing temp path without masking the original write/replace exception.

- [ ] **Step 8: Run all settings/domain tests and verify GREEN**

Run: `py -m pytest tests/test_settings.py tests/test_hotkeys.py -v`

Expected: all tests pass.

- [ ] **Step 9: Commit local settings storage**

```powershell
git add -- settings.py tests/test_settings.py
git commit -m "feat: store validated CatLocker settings locally"
```

### Task 9: Add current-user Start with Windows support

**Files:**
- Modify: `settings.py`
- Modify: `tests/test_settings.py`

- [ ] **Step 1: Write failing command and registry tests**

Append tests using a small `FakeRegistry` with `get_value`, `set_value`, and `delete_value` storage methods:

```python
from settings import RUN_KEY, STARTUP_VALUE, StartupRegistry, build_startup_command


def test_frozen_startup_command_quotes_executable_path():
    command = build_startup_command(
        executable=Path(r"C:\Program Files\CatLocker\catlocker.exe"),
        script=None,
    )
    assert command == '"C:\\Program Files\\CatLocker\\catlocker.exe" --startup'


def test_script_startup_command_uses_pythonw_and_quotes_both_paths():
    command = build_startup_command(
        executable=Path(r"C:\Program Files\Python\pythonw.exe"),
        script=Path(r"D:\My Apps\catlocker\main.py"),
    )
    assert command == '"C:\\Program Files\\Python\\pythonw.exe" "D:\\My Apps\\catlocker\\main.py" --startup'


def test_startup_state_is_derived_from_actual_registry_value():
    fake = FakeRegistry()
    startup = StartupRegistry(fake, '"C:\\CatLocker\\catlocker.exe" --startup')
    assert startup.is_enabled() is False
    startup.set_enabled(True)
    assert fake.values[(RUN_KEY, STARTUP_VALUE)] == startup.command
    assert startup.is_enabled() is True
    fake.values[(RUN_KEY, STARTUP_VALUE)] = "different command"
    assert startup.is_enabled() is False
    startup.set_enabled(False)
    assert (RUN_KEY, STARTUP_VALUE) not in fake.values


def test_registry_failure_does_not_modify_toml(tmp_path):
    path = tmp_path / "config.toml"
    save_settings(path, AppSettings("F24", False))
    original = path.read_bytes()
    startup = StartupRegistry(FakeRegistry(write_error=PermissionError("denied")), "command")
    with pytest.raises(PermissionError, match="denied"):
        startup.set_enabled(True)
    assert path.read_bytes() == original


def test_disabling_missing_startup_value_is_idempotent():
    startup = StartupRegistry(FakeRegistry(), "command")
    startup.set_enabled(False)
    assert startup.is_enabled() is False


def test_registry_read_and_delete_errors_propagate():
    with pytest.raises(PermissionError, match="read denied"):
        StartupRegistry(FakeRegistry(read_error=PermissionError("read denied")), "command").is_enabled()
    with pytest.raises(PermissionError, match="delete denied"):
        StartupRegistry(FakeRegistry(delete_error=PermissionError("delete denied")), "command").set_enabled(False)
```

- [ ] **Step 2: Run startup tests and verify RED**

Run: `py -m pytest tests/test_settings.py -k "startup or registry" -v`

Expected: collection fails because startup helpers do not exist.

- [ ] **Step 3: Implement command construction and registry adapter**

Use `subprocess.list2cmdline([str(executable), *( [str(script)] if script else [] ), "--startup"])` so Windows quoting follows Python's Windows command-line rules.

Define `RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"` and `STARTUP_VALUE = "CatLocker"`. `StartupRegistry` depends on a narrow adapter with `get_value`, `set_value`, and `delete_value`; its production adapter wraps `winreg.OpenKey/CreateKeyEx`, `QueryValueEx`, `SetValueEx(..., REG_SZ, ...)`, and `DeleteValue` under `HKEY_CURRENT_USER` only. Missing key/value means disabled. Other registry errors propagate.

`is_enabled()` returns true only when the actual stored command equals the expected current command. `set_enabled(True)` writes that exact command; false removes the value and tolerates only missing-value errors. No startup setting is read from or written to TOML.

- [ ] **Step 4: Run settings/startup tests and verify GREEN**

Run: `py -m pytest tests/test_settings.py -v`

Expected: all tests pass.

- [ ] **Step 5: Commit login startup support**

```powershell
git add -- settings.py tests/test_settings.py
git commit -m "feat: manage CatLocker login startup"
```

## Chunk 5: Native Tray and Settings UI

### Task 10: Implement native notification-area state and commands

**Files:**
- Create: `tray.py`
- Create: `tests/test_tray.py`

- [ ] **Step 1: Write failing pure tray-state tests**

Create `tests/test_tray.py`:

```python
from queue import SimpleQueue

from tray import MenuCommand, TrayAction, TrayState, build_menu_state


def test_unlocked_menu_enables_only_valid_state_actions():
    state = build_menu_state(locked=False, startup_enabled=False)
    assert state.enabled(MenuCommand.LOCK)
    assert not state.enabled(MenuCommand.UNLOCK)
    assert state.enabled(MenuCommand.SETTINGS)
    assert not state.checked(MenuCommand.STARTUP)


def test_locked_menu_keeps_mouse_unlock_available_and_disables_settings():
    state = build_menu_state(locked=True, startup_enabled=True)
    assert not state.enabled(MenuCommand.LOCK)
    assert state.enabled(MenuCommand.UNLOCK)
    assert not state.enabled(MenuCommand.SETTINGS)
    assert state.checked(MenuCommand.STARTUP)


def test_menu_selection_enqueues_action_instead_of_mutating_state():
    actions = SimpleQueue()
    state = TrayState(actions=actions, locked=True)
    state.handle_menu_command(MenuCommand.UNLOCK)
    assert actions.get_nowait() == TrayAction.UNLOCK
    assert state.locked is True
```

- [ ] **Step 2: Run tray-state tests and verify RED**

Run: `py -m pytest tests/test_tray.py -v`

Expected: collection fails with `ModuleNotFoundError: No module named 'tray'`.

- [ ] **Step 3: Implement pure menu/action state**

Create `tray.py` with stable integer `MenuCommand` IDs for Lock, Unlock, Toggle, Settings, Startup, and Exit; `TrayAction` values for the same actions; immutable menu item state; and a mutable tray presentation state containing only last-observed `locked`, startup, notifications, tooltip, and action queue. It never calls controller methods.

`build_menu_state()` must produce the enable/check rules in the tests. `handle_menu_command()` maps a selected ID to one queue insertion and does not optimistically change lock/startup state.

- [ ] **Step 4: Run tray-state tests and verify GREEN**

Run: `py -m pytest tests/test_tray.py -v`

Expected: pure tray-state tests pass.

- [ ] **Step 5: Write failing native lifecycle tests against a fake API**

Add `FakeTrayApi` that records owner thread IDs and `load_icon`, `destroy_icon`, `add_icon`, `set_version`, `modify_icon`, `delete_icon`, `show_menu`, `show_notification`, `register_taskbar_created`, `create_window`, and message-loop calls. Give it a stable owned icon handle. Add:

```python
from tray import NativeTray, TrayUpdate


def test_tray_adds_updates_and_deletes_icon_on_owner_thread(tmp_path):
    api = FakeTrayApi()
    tray = NativeTray(
        actions=SimpleQueue(),
        startup_enabled=False,
        notifications=True,
        icon_path=tmp_path / "icon.ico",
        api=api,
    )
    caller = threading.get_ident()
    tray.start(timeout=1)
    tray.post_update(TrayUpdate(locked=True))
    api.wait_until(lambda: len(api.modify_calls) == 1)
    tray.stop(timeout=1)
    assert api.add_threads == api.modify_threads == api.delete_threads
    assert api.add_threads[0] != caller
    assert api.destroy_icon_calls == [api.icon_handle]


def test_explorer_restart_readds_current_icon():
    api, tray = started_tray()
    first_count = len(api.add_calls)
    first_version_count = len(api.set_version_calls)
    api.emit_taskbar_created()
    api.wait_until(lambda: len(api.set_version_calls) == first_version_count + 1)
    assert len(api.add_calls) == first_count + 1
    assert len(api.set_version_calls) == first_version_count + 1
    tray.stop(timeout=1)


def test_state_notification_updates_tooltip_and_optional_balloon():
    api, tray = started_tray(notifications=True)
    tray.post_update(TrayUpdate(locked=True, reason="toggle"))
    api.wait_until(lambda: api.notification_calls)
    assert api.modify_calls[-1].tooltip == "CatLocker — Keyboard Locked"
    assert api.notification_calls[-1].message == "Cat Mode ON — Keyboard Locked"
    tray.stop(timeout=1)


def test_balloon_failure_is_cosmetic_and_keeps_updated_state():
    api, tray = started_tray(notifications=True, notification_error=OSError("balloon failed"))
    tray.post_update(TrayUpdate(locked=True, reason="toggle"))
    api.wait_until(lambda: api.notification_attempts == 1)
    assert api.modify_calls[-1].tooltip == "CatLocker — Keyboard Locked"
    assert tray.is_alive()
    tray.post_update(TrayUpdate(locked=False))
    api.wait_until(lambda: api.modify_calls[-1].tooltip == "CatLocker — Keyboard Unlocked")
    tray.stop(timeout=1)
```

Also test that notifications disabled produces no balloon, native menu selection only enqueues a `TrayAction`, readiness reports window/icon creation failures, and a message-loop error deletes the icon best-effort.

For each startup failure after a successful `load_icon`, assert `destroy_icon_calls == [icon_handle]`. For a load failure, assert it is empty. These tests ensure ownership cleanup occurs exactly once on success and every applicable failure path.

- [ ] **Step 6: Run native tray tests and verify RED**

Run: `py -m pytest tests/test_tray.py -k "tray or explorer or notification or failure" -v`

Expected: failures because `NativeTray`, `TrayUpdate`, and the Win32 adapter are missing.

- [ ] **Step 7: Implement the native tray adapter and owner thread**

Define pointer-sized aliases `LRESULT=ctypes.c_ssize_t`, `UINT_PTR=ctypes.c_size_t`, and `HINSTANCE/HICON/HCURSOR/HBRUSH/HMENU/HANDLE=ctypes.c_void_p`. Define the exact callback and structures:

```python
WNDPROC = ctypes.WINFUNCTYPE(
    LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
)

class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.UINT), ("style", wintypes.UINT),
        ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int), ("hInstance", HINSTANCE),
        ("hIcon", HICON), ("hCursor", HCURSOR),
        ("hbrBackground", HBRUSH), ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR), ("hIconSm", HICON),
    ]

class GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]

class _NID_UNION(ctypes.Union):
    _fields_ = [("uTimeout", wintypes.UINT), ("uVersion", wintypes.UINT)]

class NOTIFYICONDATAW(ctypes.Structure):
    _anonymous_ = ("version",)
    _fields_ = [
        ("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND),
        ("uID", wintypes.UINT), ("uFlags", wintypes.UINT),
        ("uCallbackMessage", wintypes.UINT), ("hIcon", HICON),
        ("szTip", wintypes.WCHAR * 128), ("dwState", wintypes.DWORD),
        ("dwStateMask", wintypes.DWORD), ("szInfo", wintypes.WCHAR * 256),
        ("version", _NID_UNION), ("szInfoTitle", wintypes.WCHAR * 64),
        ("dwInfoFlags", wintypes.DWORD), ("guidItem", GUID),
        ("hBalloonIcon", HICON),
    ]
```

Load `user32`, `kernel32`, and `shell32` with `use_last_error=True`. Declare exact `argtypes`/`restype`:

```python
user32.RegisterClassExW.argtypes = [ctypes.POINTER(WNDCLASSEXW)]
user32.RegisterClassExW.restype = wintypes.ATOM
user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR,
    wintypes.DWORD, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wintypes.HWND, HMENU, HINSTANCE, wintypes.LPVOID]
user32.CreateWindowExW.restype = wintypes.HWND
user32.DestroyWindow.argtypes = [wintypes.HWND]
user32.DestroyWindow.restype = wintypes.BOOL
user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.DefWindowProcW.restype = LRESULT
user32.RegisterWindowMessageW.argtypes = [wintypes.LPCWSTR]
user32.RegisterWindowMessageW.restype = wintypes.UINT
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
kernel32.GetModuleHandleW.restype = HINSTANCE
shell32.Shell_NotifyIconW.argtypes = [wintypes.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]
shell32.Shell_NotifyIconW.restype = wintypes.BOOL
user32.LoadImageW.argtypes = [HINSTANCE, wintypes.LPCWSTR, wintypes.UINT,
    ctypes.c_int, ctypes.c_int, wintypes.UINT]
user32.LoadImageW.restype = HANDLE
user32.DestroyIcon.argtypes = [HICON]
user32.DestroyIcon.restype = wintypes.BOOL
user32.CreatePopupMenu.argtypes = []
user32.CreatePopupMenu.restype = HMENU
user32.AppendMenuW.argtypes = [HMENU, wintypes.UINT, UINT_PTR, wintypes.LPCWSTR]
user32.AppendMenuW.restype = wintypes.BOOL
user32.EnableMenuItem.argtypes = [HMENU, wintypes.UINT, wintypes.UINT]
user32.EnableMenuItem.restype = wintypes.BOOL
user32.CheckMenuItem.argtypes = [HMENU, wintypes.UINT, wintypes.UINT]
user32.CheckMenuItem.restype = wintypes.DWORD
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.SetForegroundWindow.restype = wintypes.BOOL
user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
user32.GetCursorPos.restype = wintypes.BOOL
user32.TrackPopupMenu.argtypes = [HMENU, wintypes.UINT, ctypes.c_int, ctypes.c_int,
    ctypes.c_int, wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.TrackPopupMenu.restype = wintypes.UINT
user32.DestroyMenu.argtypes = [HMENU]
user32.DestroyMenu.restype = wintypes.BOOL
user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
user32.GetMessageW.restype = wintypes.BOOL
user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.TranslateMessage.restype = wintypes.BOOL
user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.DispatchMessageW.restype = LRESULT
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.PostMessageW.restype = wintypes.BOOL
```

The native implementation must:

- retain `WNDPROC`, `WNDCLASSEXW`, window handle, loaded icon handle, and `NOTIFYICONDATAW` for the thread lifetime;
- obtain `hInstance` with `GetModuleHandleW(None)` for both class registration and window creation, and obtain popup coordinates with `GetCursorPos` immediately before `TrackPopupMenu`;
- create the hidden tray window and register `TaskbarCreated` before signaling ready;
- load the existing `assets/icon.ico` with `LoadImageW(..., IMAGE_ICON, ..., LR_LOADFROMFILE)` without `LR_SHARED`, making the tray the handle owner;
- add the icon with `NIM_ADD`, immediately apply `NIM_SETVERSION` with `NOTIFYICON_VERSION_4`, and use that icon for both states initially;
- rebuild the popup menu from `build_menu_state()` on each open and use `TPM_RETURNCMD | TPM_RIGHTBUTTON`;
- enqueue the selected action and return promptly from the window procedure;
- handle a private update message by draining an unbounded update queue and applying only the newest presentation state;
- re-add the current icon on `TaskbarCreated`;
- use `NIF_INFO` only for enabled toggle/emergency notifications; catch a false return/raised balloon error as cosmetic without undoing presentation state or terminating the tray;
- always call `NIM_DELETE`, destroy the window, and `DestroyIcon` the owned handle exactly once in owner-thread cleanup.

As with the hook, readiness is set on success and failure. Treat `GetMessageW == -1` as an error. `post_update()` only enqueues and posts a private message. `stop(timeout)` posts a stop message and uses a bounded join; it never runs cleanup on the caller thread during normal operation.

Expose `post_notifications_enabled(enabled)` as a narrow convenience wrapper that enqueues a presentation update through `post_update()`; it must not mutate tray state on the caller thread. Cover it with a test that waits for the tray owner thread to observe the changed preference.

- [ ] **Step 8: Run all tray tests and verify GREEN**

Run: `py -m pytest tests/test_tray.py -v`

Expected: all tray tests pass without creating a real notification icon.

- [ ] **Step 9: Commit the native tray**

```powershell
git add -- tray.py tests/test_tray.py
git commit -m "feat: add native Windows tray controls"
```

### Task 11: Add transactional settings coordination and hotkey recording

**Files:**
- Create: `settings_window.py`
- Create: `tests/test_settings_window.py`
- Modify: `hotkeys.py`
- Modify: `tests/test_hotkeys.py`

- [ ] **Step 1: Write failing VK-to-shortcut recording tests**

Append to `tests/test_hotkeys.py`:

```python
from hotkeys import shortcut_from_pressed_vks


def test_recorded_physical_keys_normalize_to_generic_modifier_families():
    shortcut = shortcut_from_pressed_vks({VK_RCONTROL, VK_LSHIFT, 0x4B}, trigger_vk=0x4B)
    assert shortcut.canonical == "Ctrl+Shift+K"


def test_recorder_rejects_modifier_only_and_unknown_trigger():
    with pytest.raises(ShortcutError):
        shortcut_from_pressed_vks({VK_LCONTROL}, trigger_vk=VK_LCONTROL)
    with pytest.raises(ShortcutError):
        shortcut_from_pressed_vks({0xFF}, trigger_vk=0xFF)


def test_recorder_rejects_an_extra_non_modifier_key():
    with pytest.raises(ShortcutError):
        shortcut_from_pressed_vks({0x41, 0x4B}, trigger_vk=0x4B)
```

- [ ] **Step 2: Run recording conversion tests and verify RED**

Run: `py -m pytest tests/test_hotkeys.py -k "recorded or recorder" -v`

Expected: collection fails because `shortcut_from_pressed_vks` is missing.

- [ ] **Step 3: Implement reverse VK lookup and recording conversion**

Add a unique `VK_TO_NAME` reverse lookup for supported non-modifier keys. `shortcut_from_pressed_vks(pressed, trigger_vk)` rejects a modifier trigger, unknown trigger, or extra non-modifier VK; converts physical modifiers through `active_modifier_families`; builds a `Shortcut`; and calls `validate_shortcut` before returning it.

- [ ] **Step 4: Run hotkey tests and verify GREEN**

Run: `py -m pytest tests/test_hotkeys.py -v`

Expected: all hotkey tests pass.

- [ ] **Step 5: Write failing coordinator transaction tests**

Create `tests/test_settings_window.py` with fakes for controller, settings store, startup registry, warnings, and live notification application:

```python
import pytest

from controller import EngineUnhealthy
from settings import AppSettings
from settings_window import SettingsCoordinator, SettingsLocked, WarningDeclined


def test_save_validates_replaces_persists_then_applies_notifications():
    calls = []
    coordinator = make_coordinator(calls, current=AppSettings())
    saved = coordinator.save("shift+ctrl+k", False, confirm_warning=lambda messages: True)
    assert saved == AppSettings("Ctrl+Shift+K", False)
    assert calls == [
        ("replace", "Ctrl+Shift+K"),
        ("persist", AppSettings("Ctrl+Shift+K", False)),
        ("notifications", False),
    ]


def test_persistence_failure_rolls_back_shortcut_and_live_notifications():
    calls = []
    coordinator = make_coordinator(calls, persist_error=OSError("disk full"))
    with pytest.raises(OSError, match="disk full"):
        coordinator.save("K", False, confirm_warning=lambda messages: True)
    assert calls == [
        ("replace", "K"),
        ("persist", AppSettings("K", False)),
        ("replace", "F24"),
    ]
    assert coordinator.current == AppSettings("F24", True)


def test_rollback_timeout_propagates_engine_unhealthy():
    coordinator = make_coordinator(
        [], persist_error=OSError("disk full"), rollback_error=EngineUnhealthy("stalled")
    )
    with pytest.raises(EngineUnhealthy, match="stalled"):
        coordinator.save("K", False, confirm_warning=lambda messages: True)


def test_normal_rollback_rejection_enters_fail_open():
    coordinator = make_coordinator([], persist_error=OSError("disk full"), rollback_result=None)
    with pytest.raises(EngineUnhealthy, match="rollback rejected"):
        coordinator.save("K", False, confirm_warning=lambda messages: True)
    assert coordinator.controller.fail_open_calls == 1


def test_locked_state_rejects_save_before_validation_or_io():
    calls = []
    coordinator = make_coordinator(calls, locked=True)
    with pytest.raises(SettingsLocked):
        coordinator.save("K", False, confirm_warning=lambda messages: True)
    assert calls == []


def test_warning_requires_explicit_confirmation():
    coordinator = make_coordinator([], current=AppSettings())
    with pytest.raises(WarningDeclined):
        coordinator.save("Win+L", True, confirm_warning=lambda messages: False)


def test_save_while_recording_exits_before_replacement():
    calls = []
    coordinator = make_coordinator(calls, current=AppSettings())
    coordinator.begin_recording()
    calls.clear()
    coordinator.save("K", True, confirm_warning=lambda messages: True)
    assert calls[:2] == [("exit_recording", None), ("replace", "K")]
```

Define `StartupUpdateResult(enabled: bool | None, error: OSError | None)`. Add startup tests showing `set_startup_enabled()` calls the independent registry action and then reads actual state. On write failure it still attempts the read and returns both the actual value and original error; if refresh also fails, `enabled=None` and the original write error remains primary. If the write succeeds but source-of-truth refresh fails, return `StartupUpdateResult(None, refresh_error)`. It never invokes settings persistence. Add begin/cancel recording tests proving the controller acknowledgement occurs before recorder activation and exit acknowledgement occurs on cancel/focus loss.

- [ ] **Step 6: Run coordinator tests and verify RED**

Run: `py -m pytest tests/test_settings_window.py -v`

Expected: collection fails with `ModuleNotFoundError: No module named 'settings_window'`.

- [ ] **Step 7: Implement the UI-independent coordinator and recorder model**

Create `settings_window.py` with:

- `SettingsCoordinator`, which owns the current `AppSettings` value but no lock state;
- `save()`, following validate/warn/controller replace/atomic persist/live notification order and rolling the engine shortcut back on persistence failure;
- immediate `set_startup_enabled()` returning `StartupUpdateResult`; it catches the write error, best-effort refreshes actual state, and lets the UI both refresh when known and display `result.error`;
- `begin_recording()` accepted only after `controller.enter_recording()` returns true;
- `record_keydown(vk)` and `record_keyup(vk)` using exact pressed VKs, ignoring autorepeat, and finalizing a candidate when one supported non-modifier transitions down;
- `end_recording()` that always requests `controller.exit_recording()` before clearing local capture state.

If any controller operation raises `EngineUnhealthy`, clear local recording state and re-raise so the main lifecycle owner initiates fail-open shutdown. If persistence fails and rollback returns normal rejection (`None`), call `controller.enter_fail_open()`, then raise `EngineUnhealthy("shortcut rollback rejected")`; do not keep running with divergent runtime/TOML shortcuts. Saving always exits recording before replacing. A normal initial replacement rejection raises `SettingsLocked` and performs no persistence.

- [ ] **Step 8: Run coordinator/domain tests and verify GREEN**

Run: `py -m pytest tests/test_settings_window.py tests/test_hotkeys.py tests/test_settings.py -v`

Expected: all tests pass.

- [ ] **Step 9: Write failing Tk binding/view-state tests**

Add tests around a `SettingsViewModel` (no real `Tk()` required) proving:

- Record changes label/state only after coordinator acceptance.
- A Windows Tk event's numeric `keycode` is forwarded as the VK.
- Focus loss and close exit recording.
- Save is disabled whenever controller reports locked.
- Validation and persistence errors leave the previous displayed canonical hotkey.
- Registry errors refresh the checkbox from actual registry state.

- [ ] **Step 10: Run view-model tests and verify RED**

Run: `py -m pytest tests/test_settings_window.py -k "view or focus or displayed or checkbox" -v`

Expected: failures because `SettingsViewModel` is missing.

- [ ] **Step 11: Implement the minimal Tk window**

Implement `SettingsViewModel`, then a thin `SettingsWindow` that receives an existing main-thread Tk root. Use `Toplevel`, `StringVar`/`BooleanVar`, a text entry, Record/Save/Cancel buttons, notifications and startup checkboxes, and status text. Bind `<KeyPress>`, `<KeyRelease>`, `<FocusOut>`, and `WM_DELETE_WINDOW` only while recording. Use `messagebox.askyesno` for warning confirmation and `showerror` for failures. Never create a second Tk root and never call Tk from tray/hook threads.

When lock state becomes true, close/cancel recording and disable the hotkey entry, Record, and Save controls. The window may remain visible long enough to show state, but the tray Settings command is disabled while locked.

- [ ] **Step 12: Run settings UI tests and verify GREEN**

Run: `py -m pytest tests/test_settings_window.py -v`

Expected: all tests pass without opening a real window.

- [ ] **Step 13: Commit settings coordination and UI**

```powershell
git add -- hotkeys.py settings_window.py tests/test_hotkeys.py tests/test_settings_window.py
git commit -m "feat: add safe CatLocker hotkey settings"
```

## Chunk 6: Application Lifecycle, Packaging, and Release Verification

### Task 12: Compose the app and enforce fail-open shutdown ordering

**Files:**
- Replace: `main.py`
- Create: `tests/test_main.py`
- Modify: `keyboard_hook.py`
- Modify: `tray.py`
- Modify: `settings_window.py`
- Modify: `tests/test_keyboard_hook.py`
- Modify: `tests/test_tray.py`

- [ ] **Step 1: Write a bounded failing import-safety test for the legacy entry point**

Create `tests/test_main.py` without importing `main` in the pytest process:

```python
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]


def test_importing_main_has_no_gui_or_mainloop_side_effects():
    completed = subprocess.run(
        [sys.executable, "-c", "import main; print('imported')"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=2,
        check=True,
    )
    assert completed.stdout.strip() == "imported"
```

- [ ] **Step 2: Run the bounded import test and verify RED**

Run: `py -m pytest tests/test_main.py -v`

Expected in the current clean environment: FAIL with `subprocess.CalledProcessError` caused by missing legacy `pynput`. If legacy dependencies happen to be installed, the same bounded test fails with `subprocess.TimeoutExpired` when import reaches Tk `mainloop()`. Either result proves import has forbidden runtime side effects while pytest itself remains bounded.

- [ ] **Step 3: Make the legacy entry point import-safe**

Move every legacy-only import (`core`, Tk, legacy settings functions), all Tk construction, and execution inside a `legacy_main()` function, leaving only standard-library-safe definitions at module scope. Call `legacy_main()` only under `if __name__ == "__main__":`. Do not alter legacy runtime behavior yet; this change exists solely to create a testable seam.

- [ ] **Step 4: Run the import test and verify GREEN**

Run: `py -m pytest tests/test_main.py -v`

Expected: the import-safety test passes.

- [ ] **Step 5: Write failing startup and action-routing tests**

Append tests that import `AppLifecycle` and `TrayAction`. Add fake root, hook, tray, controller, startup registry, and settings window; record every call in one shared list:

```python
import threading

import pytest

from controller import EngineUnhealthy
from main import AppLifecycle, create_application
from settings import AppSettings
from settings_window import StartupUpdateResult
from tray import TrayAction


def test_startup_begins_unlocked_and_waits_for_hook_before_tray():
    app, calls = make_app()
    app.start()
    assert calls[:3] == [
        ("root_withdraw",),
        ("hook_start",),
        ("tray_start",),
    ]
    assert app.controller.locked is False


@pytest.mark.parametrize(
    ("action", "expected"),
    [
        (TrayAction.LOCK, "controller_lock"),
        (TrayAction.UNLOCK, "controller_unlock"),
        (TrayAction.TOGGLE, "controller_toggle"),
        (TrayAction.SETTINGS, "settings_show"),
        (TrayAction.STARTUP, "startup_toggle"),
    ],
)
def test_tray_actions_execute_on_main_thread(action, expected):
    app, calls = make_app()
    app.start()
    app.handle_tray_action(action)
    assert (expected, threading.get_ident()) in calls


def test_hook_install_failure_never_starts_tray_and_reports_error():
    app, calls = make_app(hook_start_error=OSError("hook failed"))
    with pytest.raises(OSError, match="hook failed"):
        app.start()
    assert ("tray_start",) not in calls
    assert ("root_show_error", "hook failed") in calls


def test_tray_start_failure_stops_hook_before_reporting():
    app, calls = make_app(tray_start_error=OSError("tray failed"))
    with pytest.raises(OSError, match="tray failed"):
        app.start()
    assert calls.index(("hook_stop",)) < calls.index(("root_show_error", "tray failed"))


def test_startup_action_refreshes_presentations_and_shows_error():
    error = PermissionError("registry denied")
    app, calls = make_app(startup_result=StartupUpdateResult(False, error))
    app.start()
    app.handle_tray_action(TrayAction.STARTUP)
    assert ("tray_startup_state", False) in calls
    assert ("settings_startup_state", False) in calls
    assert ("root_show_error", "registry denied") in calls


def test_create_application_wires_loaded_settings_and_actual_startup(tmp_path):
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        'toggle_hotkey = "Ctrl+Alt+F12"\nnotifications = false\n',
        encoding="utf-8",
    )
    factories = RecordingFactories(startup_enabled=True)
    app = create_application(
        config_path=config_path,
        executable=tmp_path / "CatLocker.exe",
        resource_root=tmp_path,
        factories=factories,
    )
    assert factories.root_withdrawn_before_components is True
    assert factories.hook_shortcut.canonical == "Ctrl+Alt+F12"
    assert factories.tray_notifications is False
    assert factories.tray_startup_enabled is True
    assert factories.coordinator_settings == AppSettings("Ctrl+Alt+F12", False)
```

- [ ] **Step 6: Run lifecycle tests and verify RED**

Run: `py -m pytest tests/test_main.py -v`

Expected: collection fails with `ImportError: cannot import name 'AppLifecycle' from 'main'`.

- [ ] **Step 7: Implement an injectable lifecycle composition root**

Replace `main.py` with definitions guarded by `if __name__ == "__main__":`. `AppLifecycle` receives constructed dependencies for tests; `create_application()` creates real ones and accepts an optional `ApplicationFactories` bundle so the complete graph can be tested without native windows/hooks.

`create_application()` constructs `KeyboardHook(shortcut)`, whose internally owned `InputState` defaults to unlocked; there is no configurable startup lock. It creates `Tk()` and calls `withdraw()` immediately, before config I/O or any component/thread startup, so no root window flashes. `start()` verifies the hook reports unlocked, starts/waits for the hook, starts/waits for the tray, schedules queue pumping with `root.after`, and then allows `run()` to enter `root.mainloop()`. In tests, `make_app()` supplies an already-created fake root and `start()` withdraws it as its first operation to preserve the same invariant.

Use this concrete composition order:

1. Create the Tk root through the factory and immediately call `withdraw()`.
2. Resolve/load `AppSettings` (or use the explicit test `config_path`) and parse its canonical shortcut.
3. Create the unbounded tray-action queue; the hook creates/exposes its engine-event queue.
4. Construct `KeyboardHook(shortcut)`—do not construct a separate `InputState`, because the hook owns it and defaults it to unlocked.
5. Construct `CatModeController(hook)`.
6. Build the current frozen/script startup command and construct `StartupRegistry`; read its actual enabled state.
7. Resolve `assets/icon.ico` from the explicit resource root or `resource_path()` and construct `NativeTray(actions, startup_enabled, settings.notifications, icon_path)`; its thread is not started yet.
8. Construct `SettingsCoordinator(current_settings, controller, save_settings callback, startup_registry, tray.post_notifications_enabled)`. The callback only enqueues a tray preference update, so there is no construction cycle or cross-thread mutation.
9. Construct `SettingsWindow(root, coordinator)`.
10. Return `AppLifecycle` with all dependencies and finite command/thread timeouts.

If any composition step after root creation fails, destroy the hidden root before re-raising. No hook or tray thread starts until `AppLifecycle.start()`.

`handle_tray_action()` runs only on the Tk/main thread and routes lock/unlock/toggle through the controller; Settings through `SettingsWindow.show`; Startup through the coordinator's independent registry operation; and Exit through `shutdown()`. For Startup, consume `StartupUpdateResult`, post the actual `enabled` value (when known) to both tray and settings presentation, and display `result.error` without changing TOML. Catch `EngineUnhealthy` from any routed operation, enter fail-open, show an error on the main thread, and shut down.

`pump_events()` drains engine events and tray actions without blocking. It forwards the authoritative locked state to `NativeTray.post_update()` and `SettingsWindow.on_lock_state()`. Fatal engine events immediately enter shutdown. Reschedule with a short fixed Tk `after` interval only while running.

- [ ] **Step 8: Run startup/action tests and verify GREEN**

Run: `py -m pytest tests/test_main.py -v`

Expected: current lifecycle tests pass without opening a real GUI or installing a hook.

- [ ] **Step 9: Write failing shutdown and fallback tests**

Append:

```python
def test_exit_unlocks_then_stops_hook_then_tray_then_tk():
    app, calls = make_app()
    app.start()
    app.handle_tray_action(TrayAction.LOCK)
    calls.clear()
    app.shutdown()
    ordered = [
        calls.index(("controller_unlock", threading.get_ident())),
        calls.index(("hook_stop",)),
        calls.index(("tray_stop",)),
        calls.index(("root_destroy",)),
    ]
    assert ordered == sorted(ordered)


def test_unlock_timeout_enters_fail_open_before_hook_stop():
    app, calls = make_app(unlock_error=EngineUnhealthy("stalled"))
    app.start()
    app.shutdown()
    assert calls.index(("controller_fail_open",)) < calls.index(("hook_stop",))


def test_hook_stop_timeout_forces_fail_open_unhook_and_quit():
    app, calls = make_app(hook_stop_error=TimeoutError("stalled"))
    app.start()
    app.shutdown()
    assert calls.index(("hook_enter_fail_open",)) < calls.index(("hook_force_unhook",))
    assert calls.index(("hook_force_unhook",)) < calls.index(("hook_post_quit",))
    assert ("root_destroy",) in calls


def test_tray_stop_timeout_best_effort_removes_icon_and_continues():
    app, calls = make_app(tray_stop_error=TimeoutError("stalled"))
    app.start()
    app.shutdown()
    assert calls.index(("tray_force_remove_icon",)) < calls.index(("root_destroy",))
```

Add idempotent double-shutdown, fatal callback event, and Exit-action tests. Every wait in fakes must receive the configured finite timeout.

- [ ] **Step 10: Run shutdown tests and verify RED**

Run: `py -m pytest tests/test_main.py -k "shutdown or exit or timeout or fatal" -v`

Expected: failures because ordered shutdown and fallback methods are missing.

- [ ] **Step 11: Implement bounded normal and fallback shutdown**

The main-thread `shutdown()` is idempotent and marks closing before work. It attempts controller force-unlock with the command timeout. Whether accepted or unhealthy, it sets terminal fail-open before stopping the hook. Then:

1. Call `hook.stop(timeout)` for owner-thread unhook and join.
2. On timeout, call thread-safe `hook.enter_fail_open()`, best-effort `hook.force_unhook()` using the retained handle under a handle lock, call `hook.post_quit()`, and retry one bounded join.
3. Call `tray.stop(timeout)` for owner-thread icon/window cleanup and join.
4. On timeout, best-effort `tray.force_remove_icon()` using the last copied `NOTIFYICONDATAW` under a presentation lock, post the tray quit message again, and retry one bounded join.
5. Destroy Tk.

Add and test `KeyboardHook.force_unhook()`/`post_quit()` and `NativeTray.force_remove_icon()`/`post_quit()` as emergency-only, idempotent methods. Create hook and tray threads with `daemon=True`; normal shutdown still performs bounded joins, while a twice-stalled native message loop cannot keep Python alive after fail-open/unhook cleanup. Normal tests must continue proving owner-thread cleanup; fallback tests assert both bounded join attempts and prove process exit cannot leave keyboard suppression active. Cleanup errors are reported but do not prevent later cleanup stages.

- [ ] **Step 12: Run lifecycle plus engine/tray regression tests and verify GREEN**

Run: `py -m pytest tests/test_main.py tests/test_controller.py tests/test_keyboard_hook.py tests/test_tray.py -v`

Expected: all tests pass.

- [ ] **Step 13: Commit the application lifecycle**

```powershell
git add -- main.py keyboard_hook.py tray.py settings_window.py tests/test_main.py tests/test_keyboard_hook.py tests/test_tray.py
git commit -m "feat: compose fail-open CatLocker lifecycle"
```

### Task 13: Remove legacy listeners and update Windows packaging

**Files:**
- Delete: `core.py`
- Delete: `format.py`
- Delete: `build.sh`
- Delete: `keylock.iss`
- Create: `catlocker.iss`
- Modify: `requirements.txt`
- Modify: `build.py`
- Modify: `build.bat`
- Modify: `.gitignore`
- Modify: `PAD.xml`

- [ ] **Step 1: Add a packaging smoke test before changing build files**

Add `tests/test_packaging.py` that reads repository files and asserts:

```python
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_runtime_has_no_third_party_dependencies():
    text = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    assert "pynput" not in text.casefold()
    assert "six" not in text.casefold()


def test_build_and_installer_use_catlocker_identity():
    combined = "\n".join(
        (ROOT / name).read_text(encoding="utf-8")
        for name in ("build.py", "build.bat", "catlocker.iss")
    )
    assert "CatLocker" in combined
    assert "keylock.exe" not in combined.casefold()
    assert "--add-data" in (ROOT / "build.py").read_text(encoding="utf-8")
    assert "assets/icon.ico" in (ROOT / "build.py").read_text(encoding="utf-8").replace("\\", "/")
    assert "check=True" in (ROOT / "build.py").read_text(encoding="utf-8")


def test_legacy_input_and_mouse_assets_are_removed():
    assert not (ROOT / "core.py").exists()
    assert not (ROOT / "assets" / "mouse_locked.png").exists()
    assert not (ROOT / "assets" / "mouse_unlocked.png").exists()
```

- [ ] **Step 2: Run packaging tests and verify RED**

Run: `py -m pytest tests/test_packaging.py -v`

Expected: collection or assertions fail because `catlocker.iss` is missing and legacy dependencies/files remain.

- [ ] **Step 3: Remove obsolete code/assets and update dependency files**

Delete `core.py`, `format.py`, `build.sh`, `assets/mouse_locked.png`, `assets/mouse_unlocked.png`, `assets/keyboard_locked.png`, `assets/keyboard_unlocked.png`, `assets/layout.png`, and obsolete button images `assets/1.png` through `assets/3.png`. Preserve `assets/icon.ico`, `assets/icon.png`, `LICENSE`, and upstream history.

Replace `requirements.txt` with a comment stating Python 3.11+ and no third-party runtime dependencies. Keep pytest/PyInstaller only in `requirements-dev.txt`. Update `.gitignore` from `keylock.toml` to both `catlocker.toml` and corrupt/temp patterns.

- [ ] **Step 4: Rename and simplify the build definitions**

Update `build.py` and `build.bat` to invoke the installed module through the same interpreter: `[sys.executable, "-m", "PyInstaller", ...]` and `py -m PyInstaller ...`. Pass `--name=CatLocker`, `--onefile`, `--noconsole`, `--icon=assets/icon.ico`, `--add-data=assets/icon.ico;assets`, and `main.py`. Remove the hard-coded virtual-environment site-packages path, NumPy exclusion, and full asset-directory bundling. `build.py` must use `subprocess.run([...], check=True)`; `build.bat` must propagate a nonzero result with `if errorlevel 1 exit /b %errorlevel%`.

Add `resource_path(relative)` in `main.py`: use `Path(sys._MEIPASS)` only when that PyInstaller attribute exists, otherwise use the source directory containing `main.py`. Pass `resource_path("assets/icon.ico")` to the tray. Extend `tests/test_packaging.py` to monkeypatch `_MEIPASS` and verify both frozen and source icon paths exist/resolve as intended.

Replace `keylock.iss` with `catlocker.iss`: retain the existing AppId and upstream author attribution, rename the product/executable/output to CatLocker, target Windows 11-compatible x64 installs, retain per-user/no-elevation installation, and point URLs to this fork only if the repository remote provides a real fork URL. Otherwise preserve the upstream source/attribution URL rather than inventing one.

Update `PAD.xml` identity/description without removing upstream attribution. Do not add an updater or network capability.

- [ ] **Step 5: Run packaging assertions and full unit suite and verify GREEN**

Run: `py -m pytest tests/test_packaging.py -v`

Expected: packaging assertions pass.

Run: `py -m pytest -v`

Expected: the complete unit suite passes.

- [ ] **Step 6: Compile and build the portable executable**

Run: `py -m compileall -q hotkeys.py keyboard_hook.py controller.py settings.py tray.py settings_window.py main.py`

Expected: exit code 0 and no output.

Run: `py build.py`

Expected: PyInstaller exits 0 and creates `dist\CatLocker.exe` without missing-module warnings relevant to CatLocker.

Run: `ISCC.exe catlocker.iss`

Expected: Inno Setup exits 0 and creates the installer artifact declared by `OutputBaseFilename`. If `ISCC.exe` is unavailable, install Inno Setup or explicitly record installer verification as blocked; do not claim installed-mode acceptance without this artifact.

- [ ] **Step 7: Commit packaging cleanup**

```powershell
git add -A -- core.py format.py build.sh keylock.iss catlocker.iss requirements.txt requirements-dev.txt build.py build.bat .gitignore PAD.xml assets/mouse_locked.png assets/mouse_unlocked.png assets/keyboard_locked.png assets/keyboard_unlocked.png assets/layout.png assets/1.png assets/2.png assets/3.png tests/test_packaging.py
git commit -m "build: package dependency-free CatLocker"
```

### Task 14: Document operation, limitations, and Windows verification

**Files:**
- Replace: `README.md`
- Create: `docs/windows-manual-test-checklist.md`

- [ ] **Step 1: Write the release documentation**

Replace the README with:

- CatLocker purpose and Windows 11-only scope.
- Tray-first workflow and default F24 Stream Deck setup.
- Configurable shortcut examples and validation/warning behavior.
- Exact Left Ctrl + Right Ctrl and tray Unlock recovery paths.
- Startup-always-unlocked guarantee.
- Settings paths for installed and portable modes.
- Transparent `WH_KEYBOARD_LL` implementation and no mouse hook.
- Secure-sequence, elevated-window/UIPI, proprietary HID, and anti-cheat compatibility limitations without evasion claims.
- No telemetry/network/update behavior.
- Python 3.11+ development, test, build, and installer commands.
- Preserved upstream Axorax/Keylock credit and GPL license link.

Create `docs/windows-manual-test-checklist.md` with checkboxes for every manual scenario in the design specification, plus tested Windows/Python/build versions and a place to record results.

- [ ] **Step 2: Verify documentation contains required safety/recovery statements**

Run:

```powershell
$required = @(
  'F24',
  'Left Ctrl.*Right Ctrl',
  'Unlock Keyboard',
  'always starts unlocked',
  'WH_KEYBOARD_LL',
  'Ctrl\+Alt\+Delete',
  'anti-cheat',
  'Axorax',
  'LICENSE'
)
foreach ($pattern in $required) {
  if (-not (rg -n -- $pattern README.md docs/windows-manual-test-checklist.md)) {
    throw "Missing required documentation topic: $pattern"
  }
}
```

Expected: each required topic appears in the output.

- [ ] **Step 3: Run automated verification once more**

Run: `py -m pytest -v`

Expected: complete suite passes with no warnings.

Run: `git diff --check`

Expected: exit code 0 and no whitespace errors.

- [ ] **Step 4: Commit documentation**

```powershell
git add -- README.md docs/windows-manual-test-checklist.md
git commit -m "docs: document CatLocker operation and recovery"
```

### Task 15: Execute the Windows manual acceptance checklist

**Files:**
- Modify: `docs/windows-manual-test-checklist.md`

- [ ] **Step 1: Launch the built executable unlocked**

Run: `Start-Process -FilePath '.\dist\CatLocker.exe'`

Expected: CatLocker appears only in the notification area and reports Keyboard Unlocked. The mouse remains usable.

- [ ] **Step 2: Complete the manual keyboard/recovery matrix**

Using Notepad plus the Stream Deck or an equivalent F24 sender, execute every checklist case: repeated F24 presses, held F24 autorepeat, Ctrl+Shift+K in both directions, exact dual-control recovery, tray Unlock, many simultaneous keys, held modifiers across transitions, function/media/volume keys, Explorer restart, notification preference, portable/installed config, startup, and exit while locked.

Expected: every applicable case passes or records a specific documented Windows/HID limitation. Never test by attempting to intercept Ctrl+Alt+Delete; verify only that CatLocker documents it as unsupported.

- [ ] **Step 3: Record environment and results**

Update the checklist with Windows build, Python version, executable hash, tested input devices, and pass/fail notes. Any functional failure returns to the relevant earlier task with a new failing automated regression test where the behavior can be isolated.

- [ ] **Step 4: Stop CatLocker and confirm cleanup**

Choose tray Exit, then verify no CatLocker process or tray icon remains and normal keyboard/mouse input works.

- [ ] **Step 5: Commit the completed acceptance record**

```powershell
git add -- docs/windows-manual-test-checklist.md
git commit -m "test: record CatLocker Windows acceptance results"
```
