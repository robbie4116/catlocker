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
2. Catlocker focuses the shortcut entry and begins capturing key presses/releases at the Settings-window level. The Record-button click that starts capture cannot therefore leave the recorder listening only to the button.
3. The existing shortcut field becomes a live preview and starts blank for the new recording.
4. Each recognized held key updates the field in canonical order. For example:

   ```text
   Ctrl
   Ctrl+Shift
   Ctrl+Shift+K
   ```

5. Releasing a modifier before the trigger removes it from the preview, allowing the user to correct the chord. While recording is active, focus leaving the shortcut entry—whether to another Settings control or another application—cancels the active recording and restores the accepted shortcut. The Settings-window key bindings are for event delivery and do not override this focus policy.
6. The first valid non-modifier trigger completes the recording. The full canonical shortcut remains in the field until the user presses Save.
7. Save is disabled during a partial recording. It becomes available after a valid trigger completes recording.
8. Cancel or closing the Settings window cancels an active recording and restores the last accepted shortcut. The title-bar close protocol is intentionally equivalent to Cancel: it also discards a completed-but-unsaved candidate and restores the last accepted shortcut. Focus loss has the same effect only while recording is active. After a valid trigger completes recording, ordinary focus loss does not discard the completed candidate; the candidate remains visible until Save or Cancel/close.
9. Unsupported keys and combinations containing more than one non-modifier remain unaccepted while capture continues. The preview shows recognized held key names and deterministic `VK_XX` labels for unknown virtual keys; these values cannot be saved. Existing validation and system-shortcut warning behavior remain unchanged.

During active recording the Entry remains focusable for keyboard event delivery, but the temporary Entry-level and Settings-window-level key handlers both return Tk's `"break"` result for every captured key press and release. The Entry-level binding handles the earlier binding tag in Tk's dispatch order; the window-level binding covers events delivered while another child has focus. The Entry's normal text insertion never runs; the field is updated only through the recording state.

### Shortcut contract

The recorder continues to accept any combination of the existing modifier families (`Ctrl`, `Alt`, `Shift`, and/or `Win`) plus exactly one supported trigger. Standalone triggers such as `F24` and `K` remain valid. Sequential key sequences and multiple non-modifier triggers are out of scope.

### Startup state

No startup-state code changes are planned. The implementation will preserve and rerun the existing unlocked-on-start regression coverage.

## Architecture and data flow

The current module boundaries remain in place:

- `hotkeys.py` remains the source of canonical modifier ordering, supported virtual-key names, and shortcut validation. Add only small reusable helpers for mapping/formatting recorder state as needed.
- `SettingsCoordinator` remains responsible for the held virtual-key set and recorder lifecycle. Its contract remains:
  - `begin_recording()` enters the active state only after the controller acknowledges it.
  - `record_keydown(vk)` ignores duplicate keydowns, updates the held set, returns a completed `Shortcut` only when exactly one valid trigger is present, and otherwise returns `None` while recording remains active.
  - `record_keyup(vk)` removes the key from the held set while recording is active. A release never completes a shortcut; completion occurs only on a keydown that produces a valid one-trigger shortcut.
  - `recording_text` formats the current held set for the live preview and returns an empty string when idle.
  - `end_recording()` exits the controller recording mode and clears the held set.
- The coordinator does not retain a completed-unsaved candidate. Once a valid trigger is accepted it exits active recording and returns the `Shortcut`; `SettingsViewModel` must set `hotkey_text` from that returned `Shortcut.canonical` before the coordinator's now-idle `recording_text` can be consulted. `_accepted_hotkey` remains the cancellation baseline.
- `SettingsViewModel` will normalize each Tk event with a named `normalize_tk_event(event) -> int` helper, refresh the live field on keydown and keyup, and keep the accepted shortcut separate for cancellation restoration. It will clear the field when active recording begins, disable Save while active recording is incomplete, preserve a completed candidate until Save/Cancel, and restore `_accepted_hotkey` on active-recording cancellation.
- `SettingsWindow` will bind temporary `<KeyPress>` and `<KeyRelease>` handlers to both the shortcut Entry and the `Toplevel`, bind `<FocusOut>` to the shortcut entry, focus the entry when recording starts, and synchronize Tk variables and control state. The permanent `WM_DELETE_WINDOW` protocol remains installed independently of temporary recording bindings.
- `main.py`, `controller.py`, and startup persistence remain unchanged.

Tk modifier events will be normalized before entering the coordinator by `normalize_tk_event(event) -> int`. The helper reads `event.keysym` first for known modifier names and falls back to `int(event.keycode)`. Keysym matching has precedence over generic keycodes. The exact modifier table is:

| Tk keysym(s) | Virtual key passed to the coordinator |
| --- | --- |
| `Shift_L`, `Shift_R` | `VK_LSHIFT`, `VK_RSHIFT` |
| `Control_L`, `Control_R` | `VK_LCONTROL`, `VK_RCONTROL` |
| `Alt_L`, `Alt_R` | `VK_LMENU`, `VK_RMENU` |
| `Win_L`, `Win_R`, `Super_L`, `Super_R`, `Meta_L`, `Meta_R` | `VK_LWIN`, `VK_RWIN` |

Keysym matching is case-insensitive after trimming. When a keysym is not one of the known aliases, generic Tk modifier keycodes `0x10`, `0x11`, and `0x12` map to `VK_LSHIFT`, `VK_LCONTROL`, and `VK_LMENU` respectively. Existing left/right virtual-key values pass through unchanged; Windows-key keycodes `0x5B` and `0x5C` also pass through unchanged. The same normalization function is used for keydown and keyup, so the value removed on release is the value added on press. Trigger keycodes continue to use the existing Windows virtual-key mapping. A missing/non-numeric `keycode` raises `ValueError` from the helper and is treated as an unrecordable event rather than persisted input. The window handlers catch this `ValueError`, leave the held-key state and display unchanged, return `"break"`, and keep recording active so the user can try another key.

The coordinator’s `recording_text` formatter will use the existing modifier order and key-name table. It will display one family name for each active modifier family, so pressing left and right Control displays only `Ctrl`, followed by recognized non-modifier names sorted by ascending virtual-key value. Unknown non-modifiers use `VK_XX` with a two-digit uppercase hexadecimal virtual-key value (or the full uppercase hexadecimal value when larger than `0xFF`). A valid one-trigger state produces the same canonical text used by `Shortcut.canonical`; an incomplete or invalid state remains a preview only and cannot be saved. If an invalid state becomes valid only after releasing an extra key, the preview updates but recording does not complete until the user presses a valid trigger keydown again.

## Error handling and lifecycle

- If the keyboard controller rejects entering recording by returning `False`, the coordinator remains idle, preserves the accepted display, and the window installs no temporary bindings. If the controller reports an `EngineUnhealthy` exception, local recording state is cleared and the existing fatal/error routing remains in effect.
- If the recorder loses focus, its temporary bindings are removed, the controller exits recording mode, and the accepted shortcut is restored. A `False` result from `exit_recording()` still clears local state and removes bindings without being treated as a fatal error; an `EngineUnhealthy` exception performs the same cleanup and is routed through the fatal callback.
- If the user cancels after a partial preview, the partial value is never persisted.
- If `exit_recording()` raises `EngineUnhealthy` during focus loss, close, or lock transition, the existing path clears local recording state in its `finally` cleanup, removes the temporary bindings, restores the accepted display, and routes the engine failure through the existing fatal callback. The UI must not leave recording controls active after this failure. Other unexpected exceptions are caught by the Settings handler, reported with `_show_error`, and handled after the same local/binding cleanup; focus-loss cleanup leaves the window open, while close cleanup still withdraws the window. In every case `_sync_controls()` runs after cleanup.
- The existing `SettingsCoordinator.save()` validation, persistence rollback, and controller replacement flow remains the single path for applying a shortcut.
- Temporary recording bindings are removed after completion, cancellation, focus loss, lock transition, and window close. The permanent close protocol remains installed so the Settings window can be reused.

## Test plan

Add failing tests before implementation, then use focused red-green cycles:

- `tests/test_settings_window.py` coordinator coverage for live modifier previews, key-release updates, standalone trigger capture, `Ctrl+Shift+K` completion, and rejection of extra non-modifier keys.
- `tests/test_settings_window.py` view-model coverage for Tk modifier normalization, blank-on-start, live field updates, Save disabled during partial recording, completed preview persistence until Save, and cancel/focus-loss restoration.
- `tests/test_settings_window.py` window coverage proving recording focuses the entry, binds temporary handlers to both the Entry and Settings window, returns `"break"` to prevent Entry insertion, and removes every temporary binding on completion, cancellation, focus loss, lock transition, and controller-exit failure.
- Normalization/formatting coverage will assert keysym precedence, left/right and generic modifier aliases, matching keyup normalization, duplicate modifier-family display, unknown `VK_XX` display, and the rule that releasing an extra key does not complete a recording.
- Failure-path coverage will distinguish a benign `False` controller response from `EngineUnhealthy`, asserting cleanup, display restoration, control synchronization, and fatal routing as appropriate. It will also cover malformed Tk events, key-release `"break"` behavior, window-level dispatch when another child has focus, and title-bar close after a completed-but-unsaved capture.

The key state transitions are:

| State | Event | Result |
| --- | --- | --- |
| Idle/accepted | Begin accepted | Active recording, field blank, Save disabled |
| Active/partial | Modifier keydown or keyup | Held set and preview update; remain active |
| Active/invalid | Unknown or extra non-modifier keydown | Deterministic preview; remain active; Save disabled |
| Active/partial | Focus loss, Cancel, or close | Exit recording, restore accepted field, remove bindings |
| Active/partial | Valid trigger keydown | Return `Shortcut`, exit recording, set field to `Shortcut.canonical`, Save enabled |
| Completed/unsaved | Focus loss | Keep candidate visible |
| Completed/unsaved | Cancel or close | Restore accepted field |
| Completed/unsaved | Save | Validate/persist through existing save flow; accepted baseline becomes new shortcut |
| Any active state | Lock transition | Exit recording, restore accepted field, remove bindings, disable/withdraw as existing lock flow requires |
| Any active state | `EngineUnhealthy` | Clear local state, remove bindings, synchronize controls, route fatal callback |
- Existing `tests/test_main.py` startup coverage will be rerun to confirm the application still begins unlocked.

Verification after implementation:

```powershell
py -m pytest tests/test_hotkeys.py tests/test_settings_window.py tests/test_main.py -v
py -m pytest -v
py -m compileall -q hotkeys.py settings_window.py main.py tests
git diff --check
```

The final report will distinguish automated verification from physical keyboard/Stream Deck testing, which is not available in the current environment.
