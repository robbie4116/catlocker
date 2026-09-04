# Read-only keybind display design

## Context

CatLocker currently uses a Tk `Entry` to display the configured toggle shortcut. The recorder already captures key combinations and suppresses captured key events while recording, but the Entry is still a normal editable text control whenever the recorder is idle. That allows users to type literal text into a field that should represent a keyboard shortcut.

## Goal

Make the shortcut field behave like an OBS-style keybind recorder: the user can only change it through the Record shortcut flow, and the field displays the canonical combination being captured rather than receiving text input.

## Design

- Keep the existing Entry and its current layout, because it already supports the live preview and accessibility behaviors used by the settings window.
- Treat the Entry as a display surface. It is `readonly` whenever the recorder is idle, after a shortcut completes, while the window is shown normally, and after cancellation or lock transition. It is `disabled` while settings are locked.
- Keep the existing temporary Entry and Toplevel key bindings during recording. The key press and release handlers update `hotkey_var` from the view model and return `"break"` for every captured event so Tk's default Entry class bindings cannot insert, delete, or otherwise edit text.
- Update `SettingsWindow._sync_controls()` so the shortcut Entry uses `state="disabled"` when `view.locked` is true and `state="readonly"` for every unlocked state, including active recording. The view's `hotkey_enabled` remains the semantic unlocked/locked flag; the window owns the Tk-specific state mapping.
- Continue to clear the display when recording begins, show partial combinations such as `Ctrl+Shift`, and show the completed canonical combination such as `Ctrl+Shift+K` until the user saves or cancels.
- Programmatic `StringVar.set()` calls remain the source of truth for the displayed value. Save continues to validate the displayed canonical value through the existing coordinator.

## State transitions

| State | Entry state | How the value can change |
| --- | --- | --- |
| Idle/unlocked | `readonly` | Record flow or programmatic synchronization |
| Recording | `readonly` | Captured key press/release events update the variable |
| Locked | `disabled` | Programmatic synchronization only |
| Completed candidate | `readonly` | Save, Record again, Cancel, or programmatic synchronization |

The recorder remains active independently of the Entry's editable state. Keeping the Entry readonly during capture ensures the UI never falls back to literal text insertion, while the explicit event bindings continue to receive the keyboard events needed for capture. The view model remains the source of truth for whether recording is active; the Entry state is presentation-only.

## Error and cleanup behavior

Existing cleanup paths remain authoritative. Completion, cancellation, focus loss, lock transition, binding failure, and close must remove temporary bindings, restore the accepted shortcut when appropriate, synchronize the Tk variable, and leave the Entry in the state derived from the current view state. Where a cleanup operation can raise, cleanup must still attempt (in order) to end/cancel coordinator recording, restore the accepted view value, remove temporary bindings, synchronize the Tk variable, apply the locked/readonly Entry state, and then route the original error. Malformed Tk events remain ignored at the window boundary without changing recorder state.

The `_save()` read from `hotkey_var` is synchronization of the display into the already display-backed view model; it is not a supported editing path. Since the Entry is never normal/editable, save must only receive values produced by the recorder or existing programmatic state synchronization. Any value still passes through the coordinator's existing parser and validator.

## Verification

Add regression tests that assert:

1. The initial unlocked shortcut field is readonly, and a lock-before-construction field is disabled.
2. The recording path still displays partial and completed combinations and returns `"break"` for key press/release events while remaining readonly.
3. A fake Entry dispatch that would normally insert text cannot modify the displayed shortcut outside recording, while programmatic `StringVar.set()` still updates it.
4. Completion, cancel, focus loss, close, binding failure, and lock cleanup restore the expected readonly or disabled state, including after routed errors.
5. Save synchronizes and validates the displayed canonical value without creating a direct text-edit path.
6. Existing settings, recorder, error-routing, and persistence tests remain green.
