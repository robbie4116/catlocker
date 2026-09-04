# Keybind Recorder Preview Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Catlocker accept modifier-plus-trigger shortcuts reliably, show the held combination live in the existing shortcut field, and preserve the already-implemented unlocked-on-start behavior.

**Architecture:** Extend the existing `hotkeys.py` key-name/formatting utilities and `SettingsCoordinator` preview state, then adapt `SettingsViewModel` and `SettingsWindow` to normalize Tk events, update the same field during recording, and cleanly separate accepted values from drafts. Keep startup, controller, persistence, and low-level hook behavior unchanged.

**Tech Stack:** Python 3, Tkinter, pytest, Windows virtual-key constants, existing Catlocker coordinator/controller layers.

**Spec:** `docs/superpowers/specs/2026-09-04-keybind-recorder-design.md`

---

## Chunk 1: Recorder formatting, state, and Tk event normalization

### Task 1: Add deterministic recorder preview and event normalization

**Files:**
- Modify: `hotkeys.py:1-220` for preview formatting using existing VK and modifier tables.
- Modify: `settings_window.py:1-180` for `SettingsCoordinator.recording_text` and `normalize_tk_event(event)`.
- Test: `tests/test_hotkeys.py:1-155`.
- Test: `tests/test_settings_window.py:1-300`.

- [ ] **Step 1: Write failing hotkeys and coordinator tests**

Add tests in `tests/test_hotkeys.py` using `import hotkeys` and attribute lookup inside each test so the RED run reports missing behavior rather than failing test collection. Add the coordinator test in `tests/test_settings_window.py`, reusing its existing `make_coordinator()` helper and importing only already-existing symbols.

Define the smallest required behavior:

```python
def test_format_pressed_vks_orders_modifiers_and_deduplicates_families():
    assert hotkeys.format_pressed_vks({hotkeys.VK_LCONTROL, hotkeys.VK_RCONTROL, hotkeys.VK_LSHIFT, 0x4B}) == "Ctrl+Shift+K"


def test_format_pressed_vks_labels_unknown_virtual_keys():
    assert hotkeys.format_pressed_vks({0xFF}) == "VK_FF"


def test_format_pressed_vks_sorts_mixed_known_and_unknown_keys_by_vk():
    assert hotkeys.format_pressed_vks({0x100, 0xFF, 0x4B, 0x41}) == "A+K+VK_FF+VK_100"


def test_format_pressed_vks_uses_full_modifier_order():
    pressed = {hotkeys.VK_LCONTROL, hotkeys.VK_LMENU, hotkeys.VK_LSHIFT, hotkeys.VK_LWIN, 0x4B}
    assert hotkeys.format_pressed_vks(pressed) == "Ctrl+Alt+Shift+Win+K"


def test_coordinator_exposes_live_recording_text_and_clears_after_completion():
    coordinator = make_coordinator([])
    assert coordinator.begin_recording() is True
    assert coordinator.recording_text == ""
    coordinator.record_keydown(VK_LCONTROL)
    assert coordinator.recording_text == "Ctrl"
    coordinator.record_keydown(VK_LSHIFT)
    assert coordinator.recording_text == "Ctrl+Shift"
    shortcut = coordinator.record_keydown(0x4B)
    assert shortcut.canonical == "Ctrl+Shift+K"
    assert coordinator.recording_text == ""
```

Also add a release/retry case in `tests/test_settings_window.py` proving that an unknown `0xFF` plus held `K` remains invalid, releasing `0xFF` changes the preview to `K` but does not complete recording, and releasing/repressing `K` is required before the returned shortcut completes the recording.

- [ ] **Step 2: Run the focused tests and verify they fail for the intended reason**

Run:

```powershell
py -m pytest tests/test_hotkeys.py tests/test_settings_window.py -k "format_pressed or recording_text or release" -v
```

Expected: failures report missing `format_pressed_vks` and/or `recording_text`, not collection errors.

- [ ] **Step 3: Implement the minimal formatting and coordinator API**

In `hotkeys.py`, add `format_pressed_vks(pressed: set[int]) -> str` after the existing modifier-family helpers. It must:

- emit one name per active modifier family in `MODIFIER_ORDER`, so left/right variants do not duplicate `Ctrl`, `Alt`, `Shift`, or `Win`;
- emit all non-modifier names sorted as one list by ascending VK, using known names from `VK_TO_NAME` and unknown names as `VK_{vk:02X}`;
- return `""` for an empty set.

In `SettingsCoordinator`, add a read-only `recording_text` property that returns the formatter output while recording and `""` while idle. Do not change `record_keydown()`’s completed-`Shortcut` return contract or make key release complete a shortcut.

- [ ] **Step 4: Run the focused tests and verify they pass**

Run the same focused command from Step 2. Expected: all selected tests pass.

- [ ] **Step 5: Add failing normalization tests**

Add parametrized tests in `tests/test_settings_window.py` using `import settings_window as settings_window_module` and looking up `settings_window_module.normalize_tk_event` inside test bodies, so RED is a test failure rather than an import-time collection error. Cover:

- `Control_L`, `Control_R`, `Shift_L`, `Shift_R`, `Alt_L`, `Alt_R`;
- `Win_L`, `Win_R`, `Super_L`, `Super_R`, `Meta_L`, `Meta_R`;
- bare `Control`, `Ctrl`, `Shift`, `Alt`, `Win`, `Super`, `Meta` mapping to representative left-side VKs;
- generic fallback keycodes `0x10`, `0x11`, `0x12`;
- keysym precedence over a conflicting numeric keycode, including a malformed keycode when the keysym is recognized;
- recognized keysyms with a missing keycode, plus whitespace and case normalization;
- ordinary trigger keycodes passing through unchanged; and
- existing left/right and Windows VK values passing through unchanged; and
- missing/non-numeric fallback keycodes raising `ValueError`.

For the missing-keycode case, construct `SimpleNamespace(keysym="Control_L")` without a `keycode` attribute and assert it still returns `VK_LCONTROL`; use `FakeTkEvent(..., keysym="")` with `keycode=None` or a non-numeric string for the fallback error cases.

Use the existing `FakeTkEvent` helper and import the exact VK constants from `hotkeys.py`.

- [ ] **Step 6: Run normalization tests to verify RED**

Run:

```powershell
py -m pytest tests/test_settings_window.py -k "normalize_tk_event" -v
```

Expected: failures report the missing normalizer or missing mappings.

- [ ] **Step 7: Implement the explicit normalizer**

Add `normalize_tk_event(event) -> int` in `settings_window.py`. Trim and case-fold `str(getattr(event, "keysym", "") or "")`; apply the exact left/right and bare-alias table from the spec first. A recognized keysym returns its mapped VK without consulting `event.keycode`, even if that attribute is absent, `None`, or malformed. If no keysym alias matches, convert `getattr(event, "keycode")` with `int()` inside an `except (AttributeError, TypeError, ValueError)` wrapper that raises `ValueError`, map generic `0x10`, `0x11`, and `0x12` to representative left-side modifier VKs, and otherwise return the numeric VK.

- [ ] **Step 8: Run all Chunk 1 tests and verify GREEN**

Run:

```powershell
py -m pytest tests/test_hotkeys.py tests/test_settings_window.py -v
```

Expected: all selected tests pass and the pre-existing recorder, settings, and parser tests remain green.

- [ ] **Step 9: Commit Chunk 1**

```powershell
git add -- hotkeys.py settings_window.py tests/test_hotkeys.py tests/test_settings_window.py
git commit -m "feat: expose live shortcut recording state"
```

Chunk 1 does not install or remove temporary bindings and does not change Tk binding dispatch or Entry suppression. Chunk 2 owns installing, suppressing, and removing both Entry-level and Toplevel-level temporary bindings.

## Chunk 2: Settings view and Tk window integration

### Task 2: Wire live preview, focus, suppression, and cleanup into Settings

**Files:**
- Modify: `settings_window.py:177-540` for view-model behavior, bindings, control state, and failure cleanup.
- Test: `tests/test_settings_window.py:1-700` for fake observability, view-model behavior, window dispatch, and failure paths.

- [ ] **Step 1: Extend only the test fakes and dispatch helper**

Extend `FakeTkWidget` to record `focus_set()` calls and `FakeTkVariable` to record each `set()` value. Add a configurable `exit_recording_result` to `FakeController`, defaulting to `True`, and make `exit_recording()` return it so tests can exercise benign `False` responses independently from `exit_recording_error`. Add a small test-only `dispatch_child_event(child, parent, sequence, event)` helper that invokes the child binding first, stops on `"break"`, and otherwise invokes the parent binding.

Run the existing settings-window tests before adding feature assertions:

```powershell
py -m pytest tests/test_settings_window.py -v
```

Expected: the pre-existing settings-window tests pass, proving the observability changes do not alter current behavior.

- [ ] **Step 2: Write failing view-model tests**

Add tests for:

- `test_view_begin_recording_clears_display`: `SettingsViewModel.begin_recording()` clears the displayed draft after controller acceptance;
- `test_view_updates_live_preview`: `Control_L` and `Shift_L` update `hotkey_text` to `Ctrl` then `Ctrl+Shift`, releasing Shift changes it back to `Ctrl`, and `K` completes it as `Ctrl+K`; use conflicting `keycode=0xFF`/`0xFE` values with recognized keysyms to prove the view uses `normalize_tk_event()` on both press and release;
- `test_view_save_is_disabled_during_partial_recording`: Save is disabled during active partial recording and enabled after completion;
- `test_view_new_recording_replaces_unsaved_candidate`: starting a new recording replaces an unsaved candidate, with Cancel restoring the last accepted shortcut;
- `test_view_cancel_restores_accepted`: cancellation after a partial preview restores `_accepted_hotkey`;
- `test_view_false_exit_preserves_draft`: a benign `False` exit preserves a valid shortcut draft while still clearing active recording state;
- `test_view_completion_failure_restores_accepted`: `EngineUnhealthy` and ordinary completion exceptions restore the accepted value, mark recording inactive, and re-raise for window routing.

Example of the primary live-preview test:

```python
def test_view_updates_live_preview():
    coordinator = make_coordinator([])
    view = SettingsViewModel(coordinator)

    assert view.begin_recording() is True
    assert view.hotkey_text == ""
    view.on_key_press(FakeTkEvent(0xFF, keysym="Control_L"))
    assert view.hotkey_text == "Ctrl"
    view.on_key_press(FakeTkEvent(0xFF, keysym="Shift_L"))
    assert view.hotkey_text == "Ctrl+Shift"
    view.on_key_release(FakeTkEvent(0xFE, keysym="Shift_L"))
    assert view.hotkey_text == "Ctrl"
    view.on_key_press(FakeTkEvent(0x4B, keysym="k"))
    assert view.hotkey_text == "Ctrl+K"
    assert view.recording is False
```

- [ ] **Step 3: Run the named view-model tests and verify RED**

Run:

```powershell
py -m pytest tests/test_settings_window.py -k "view_begin_recording_clears_display or view_updates_live_preview or view_save_is_disabled_during_partial_recording or view_new_recording_replaces_unsaved_candidate or view_cancel_restores_accepted or view_false_exit_preserves_draft or view_completion_failure_restores_accepted" -v
```

Expected: failures show that the field is not cleared/live-updated, generic Tk modifiers are not normalized through the view, Save remains enabled during partial capture, or view-level draft restoration is missing.

- [ ] **Step 4: Implement view-model state and event handling**

Update `SettingsViewModel` as follows:

- `save_enabled` returns false while locked or actively recording;
- `begin_recording()` clears `hotkey_text` only after the coordinator accepts recording;
- `on_key_press()` normalizes the event, calls `record_keydown()`, updates `hotkey_text` from `recording_text` for partial/invalid states, and uses the returned `Shortcut.canonical` for completion before marking recording inactive;
- `on_key_release()` normalizes the event, calls `record_keyup()`, and refreshes `hotkey_text` from `recording_text` while active;
- cancellation uses `finally` to mark local recording inactive and restore `_accepted_hotkey`, even when controller exit raises; and
- malformed-event `ValueError` handling remains at the window handler boundary so a bad event is ignored without changing recorder state.

For completion-path `EngineUnhealthy` or unexpected exceptions, restore `_accepted_hotkey`, mark recording inactive, and re-raise for the window to route. A controller `False` exit result still performs normal cleanup and does not become a fatal error.

- [ ] **Step 5: Run the named view-model tests and verify GREEN**

```powershell
py -m pytest tests/test_settings_window.py -k "view_begin_recording_clears_display or view_updates_live_preview or view_save_is_disabled_during_partial_recording or view_new_recording_replaces_unsaved_candidate or view_false_exit_preserves_draft or view_completion_failure_restores_accepted" -v
```

Expected: all named view-model tests pass.

- [ ] **Step 6: Write failing window binding and cleanup tests**

Add tests for:

- `test_settings_window_recording_focuses_entry_and_binds_both`: `_record()` focuses the Entry, sets `hotkey_var` to the blank draft (`""`), and installs temporary handlers on both the Entry and the Toplevel;
- `test_settings_window_rejected_recording_preserves_value_and_bindings`: a rejected recording preserves the current field and installs no temporary bindings;
- `test_settings_window_dispatch`: the dispatch helper proves an Entry event is handled once and stopped by Entry-level `"break"`, while an event from another child reaches the Toplevel binding;
- `test_settings_window_handlers_return_break_and_synchronize`: Entry and Toplevel key handlers return `"break"`, update `hotkey_var` on each partial keydown/keyup and completion, and leave normal Entry insertion suppressed;
- `test_settings_window_focus_loss_cancels_active_recording`: focus loss during an active partial recording restores the accepted shortcut;
- `test_settings_window_close_discards_unsaved_candidate`: title-bar close restores the accepted shortcut after a completed-but-unsaved candidate, while ordinary focus loss leaves that candidate visible;
- `test_settings_window_malformed_event_is_ignored`: malformed Tk events leave state/display unchanged, return `"break"`, and keep recording active;
- `test_settings_window_cleanup`: temporary binding removal, `hotkey_var` restoration, and control synchronization occur on completion, Cancel, focus loss, lock transition, and close;
- `test_settings_window_false_exit`: a benign `False` exit result cleans up without fatal routing; and
- `test_settings_window_engine_unhealthy_and_ordinary_exception`: `EngineUnhealthy` versus ordinary exceptions during cancellation and completion perform binding cleanup, field restoration, control synchronization, and fatal/error routing. Assert the permanent `WM_DELETE_WINDOW` protocol remains installed;
- `test_settings_window_lock_ordinary_exception`: an ordinary lock-transition exit exception is shown with `_show_error()`, while bindings, field, controls, and withdrawal are reconciled; and
- `test_settings_window_lock_engine_unhealthy`: a lock-transition `EngineUnhealthy` exit exception performs the same cleanup, withdraws the window, preserves the permanent close protocol, and routes the fatal callback; and
- `test_settings_window_close_failure_restores_and_withdraws`: parametrized ordinary and `EngineUnhealthy` close failures restore/synchronize the field and controls, remove bindings, withdraw the window, and route ordinary versus fatal errors correctly.

- [ ] **Step 7: Run the named window tests and verify RED**

```powershell
py -m pytest tests/test_settings_window.py -k "settings_window_recording_focuses_entry or settings_window_rejected_recording or settings_window_dispatch or settings_window_handlers_return_break or settings_window_focus_loss_cancels or settings_window_close_discards or settings_window_malformed_event or settings_window_cleanup or settings_window_false_exit or settings_window_engine_unhealthy or settings_window_ordinary_exception or settings_window_lock_ordinary_exception or settings_window_lock_engine_unhealthy or settings_window_close_failure_restores" -v
```

Expected: failures show missing focus, binding, synchronization, suppression, or failure-cleanup behavior.

- [ ] **Step 8: Implement window focus, bindings, suppression, and cleanup**

Update `SettingsWindow` as follows:

- `_record()` binds the temporary key handlers, focuses `hotkey_entry`, calls `hotkey_var.set(self.view.hotkey_text)` for the cleared view draft, and synchronizes controls. Its test must assert that the accepted recording start visibly clears the Tk field by recording a `""` variable update;
- bind `<KeyPress>` and `<KeyRelease>` to both `hotkey_entry` and `self.window`, with the Entry binding handling Tk’s earlier dispatch tag and the Toplevel binding covering child focus; the handlers return `"break"` on every captured key event;
- keep `<FocusOut>` on `hotkey_entry` to cancel only active recordings; once a valid trigger completes, focus loss no longer discards the candidate;
- `_on_key_press()` calls `hotkey_var.set(self.view.hotkey_text)` after every successful view update, catches malformed-event `ValueError` without changing state, routes `EngineUnhealthy` through `_handle_engine_unhealthy()`, shows ordinary exceptions through `_show_error()`, and always unbinds/synchronizes after terminal failures;
- `_on_key_release()` returns `"break"`, calls `hotkey_var.set(self.view.hotkey_text)` after every successful release update, ignores malformed events safely, and follows the same unhealthy/ordinary-error cleanup policy;
- `_unbind_recording_events()` removes all temporary Entry/Toplevel bindings but leaves the permanent close protocol installed;
- `_close()` treats title-bar close and Cancel identically, restoring the accepted value for both partial and completed-unsaved drafts, calls `hotkey_var.set(self.view.hotkey_text)` after restoration, catches `EngineUnhealthy` through the fatal callback, catches ordinary exceptions with `_show_error()`, then unbinds, synchronizes, and withdraws in a `finally` cleanup even if exit raises; and
- `on_lock_state()` handles an ordinary `exit_recording()` exception by unbinding, restoring/synchronizing the field and controls, withdrawing the window, and showing `_show_error()`; an `EngineUnhealthy` exception uses the existing fatal callback;
- all cleanup paths call `hotkey_var.set(self.view.hotkey_text)` and `_sync_controls()` after view restoration. Focus-loss cleanup leaves the window open; close cleanup withdraws even on an ordinary exception; lock cleanup continues to withdraw/disable Settings as already implemented. EngineUnhealthy routes through the fatal callback, while other exceptions use `_show_error()`.

- [ ] **Step 9: Run the named window tests and verify GREEN**

Run:

```powershell
py -m pytest tests/test_settings_window.py -k "settings_window_recording_focuses_entry or settings_window_rejected_recording or settings_window_dispatch or settings_window_handlers_return_break or settings_window_focus_loss_cancels or settings_window_close_discards or settings_window_malformed_event or settings_window_cleanup or settings_window_false_exit or settings_window_engine_unhealthy or settings_window_ordinary_exception or settings_window_lock_ordinary_exception or settings_window_lock_engine_unhealthy or settings_window_close_failure_restores" -v
```

Expected: all named window, binding, error-routing, and lifecycle tests pass.

- [ ] **Step 10: Run the complete settings-window file after integration**

```powershell
py -m pytest tests/test_settings_window.py -v
```

Expected: every existing and new settings-window test passes.

- [ ] **Step 11: Commit Chunk 2**

```powershell
git add -- settings_window.py tests/test_settings_window.py
git commit -m "fix: make shortcut recording interactive"
```

## Chunk 3: Full regression verification

### Task 3: Verify recorder behavior and preserve unlocked startup

**Files:**
- No production changes planned.
- Test: existing `tests/test_main.py`, plus any narrowly scoped regression tests needed by Tasks 1-2.

- [ ] **Step 1: Run the complete test suite**

```powershell
py -m pytest -v
```

Expected: all tests pass with zero failures; the suite includes the existing `test_startup_begins_unlocked_and_waits_for_hook_before_tray` regression.

- [ ] **Step 2: Compile production and test modules**

```powershell
py -m compileall -q hotkeys.py keyboard_hook.py settings.py controller.py tray.py settings_window.py main.py build.py tests
```

Expected: exit code 0 and no output.

- [ ] **Step 3: Run repository hygiene checks**

```powershell
git diff --check
git status --short --branch
```

Expected: no whitespace errors; only the intended implementation/spec/plan commits are present and no unrelated files are modified.

Also verify the implementation did not change startup or engine composition files:

```powershell
$forbidden = git diff --name-only f90ecaa..HEAD | Where-Object { $_ -in @('main.py', 'controller.py', 'keyboard_hook.py', 'settings.py') }
if ($forbidden) { $forbidden; throw "Startup/engine files changed outside the approved scope." }
```

- [ ] **Step 4: Manually verify the supported interaction if native UI is available**

First run the focused startup regression explicitly: `py -m pytest tests/test_hotkeys.py tests/test_settings_window.py tests/test_main.py -v`; then run the complete suite from Step 1. Open Settings, click Record shortcut, press `Ctrl+Shift+K` while observing `Ctrl`, `Ctrl+Shift`, and `Ctrl+Shift+K`, release/retry a modifier, Save, reopen Settings, and verify the saved value. Confirm launch remains unlocked. Automated verification can pass without physical keyboard/Stream Deck validation; if the native UI bridge cannot target the Settings window, leave this manual check pending rather than claiming it passed.
