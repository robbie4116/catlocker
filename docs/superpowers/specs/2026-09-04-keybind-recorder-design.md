# Keybind Recorder Preview Design

**Date:** 2026-09-04

## Goal

Make Catlocker’s settings recorder reliably accept the existing modifier-plus-trigger shortcut format and show the in-progress combination in the existing shortcut field while keys are pressed. Preserve the existing startup behavior in which Catlocker launches unlocked.

## Current findings

- `SettingsCoordinator` already tracks the currently pressed virtual keys, accepts modifier families plus one trigger, and returns a completed `Shortcut` when the trigger is pressed.
- `SettingsViewModel` only updates its display after a completed `Shortcut` is returned, so modifier-only progress is invisible.
- `SettingsWindow` binds recording handlers to the entry widget. Clicking the Record button can leave focus on the button, preventing later key events from reaching those handlers.
- Tk on Windows can report generic modifier keycodes/keysyms such as Control, Alt, Shift, and Win, while the keyboard engine uses the left/right Windows virtual-key values. The recorder currently forwards the raw Tk numeric keycode, so modifier combinations may be rejected as if the modifier were an extra trigger.
- Startup is already explicitly unlocked: the keyboard hook defaults to `locked=False`, application startup rejects a hook that reports locked, and lock state is not persisted in configuration. Existing tests cover this behavior.

## Approved behavior

### Recording interaction

1. The user opens Settings and clicks `Record shortcut`.
2. Catlocker focuses the shortcut entry and begins capturing at the Settings-window level so focus on the button cannot prevent capture.
3. The existing shortcut field becomes a live preview and starts blank for the new recording.
4. Each recognized held key updates the field in canonical order. For example:

   ```text
   Ctrl
   Ctrl+Shift
   Ctrl+Shift+K
   ```

5. Releasing a modifier before the trigger removes it from the preview, allowing the user to correct the chord.
6. The first valid non-modifier trigger completes the recording. The full canonical shortcut remains in the field until the user presses Save.
7. Save is disabled during a partial recording. It becomes available after a valid trigger completes recording.
8. Cancel, focus loss, or closing the Settings window cancels the recording and restores the last accepted shortcut.
9. Unsupported keys and combinations containing more than one non-modifier remain unaccepted while capture continues. Existing validation and system-shortcut warning behavior remain unchanged.

### Shortcut contract

The recorder continues to accept any combination of the existing modifier families (`Ctrl`, `Alt`, `Shift`, and/or `Win`) plus exactly one supported trigger. Standalone triggers such as `F24` and `K` remain valid. Sequential key sequences and multiple non-modifier triggers are out of scope.

### Startup state

No startup-state code changes are planned. The implementation will preserve and rerun the existing unlocked-on-start regression coverage.

## Architecture and data flow

The current module boundaries remain in place:

- `hotkeys.py` remains the source of canonical modifier ordering, supported virtual-key names, and shortcut validation. Add only small reusable helpers for mapping/formatting recorder state as needed.
- `SettingsCoordinator` remains responsible for the held virtual-key set and recorder lifecycle. It will expose the current preview text while retaining the existing completed-`Shortcut` return behavior.
- `SettingsViewModel` will normalize each Tk event to a Catlocker virtual key, refresh the live field on keydown and keyup, and keep the accepted shortcut separate for cancellation restoration.
- `SettingsWindow` will bind temporary key handlers to the `Toplevel`, focus the entry when recording starts, and synchronize Tk variables and control state.
- `main.py`, `controller.py`, and startup persistence remain unchanged.

Tk modifier events will be normalized before entering the coordinator. Modifier keysyms will identify left/right variants where available; generic Tk modifier keycodes will map to a representative left-side virtual key in the existing modifier family. Trigger keycodes continue to use the existing Windows virtual-key mapping.

The coordinator’s preview formatter will use the existing modifier order and key-name table. It will display the currently held modifiers and recognized non-modifier keys deterministically. A valid one-trigger state produces the same canonical text used by `Shortcut.canonical`; an incomplete or invalid state remains a preview only and cannot be saved.

## Error handling and lifecycle

- If the keyboard controller rejects entering recording or reports an unhealthy engine, local recording state is cleared and the existing fatal/error routing remains in effect.
- If the recorder loses focus, its temporary bindings are removed, the controller exits recording mode, and the accepted shortcut is restored.
- If the user cancels after a partial preview, the partial value is never persisted.
- The existing `SettingsCoordinator.save()` validation, persistence rollback, and controller replacement flow remains the single path for applying a shortcut.
- Temporary recording bindings are removed after completion, cancellation, focus loss, lock transition, and window close. The permanent close protocol remains installed so the Settings window can be reused.

## Test plan

Add failing tests before implementation, then use focused red-green cycles:

- `tests/test_settings_window.py` coordinator coverage for live modifier previews, key-release updates, standalone trigger capture, `Ctrl+Shift+K` completion, and rejection of extra non-modifier keys.
- `tests/test_settings_window.py` view-model coverage for Tk modifier normalization, blank-on-start, live field updates, Save disabled during partial recording, completed preview persistence until Save, and cancel/focus-loss restoration.
- `tests/test_settings_window.py` window coverage proving recording focuses the entry and binds the temporary handlers to the Settings window.
- Existing `tests/test_main.py` startup coverage will be rerun to confirm the application still begins unlocked.

Verification after implementation:

```powershell
py -m pytest tests/test_hotkeys.py tests/test_settings_window.py tests/test_main.py -v
py -m pytest -v
py -m compileall -q hotkeys.py settings_window.py main.py tests
git diff --check
```

The final report will distinguish automated verification from physical keyboard/Stream Deck testing, which is not available in the current environment.

