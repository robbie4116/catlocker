# Read-only keybind display Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the settings shortcut field display-only so users can change the shortcut only through the recorder, with no literal text insertion.

**Architecture:** Preserve the existing `SettingsViewModel`/`SettingsWindow` recorder flow. The view model remains responsible for the canonical shortcut value and recording state; `SettingsWindow._sync_controls()` maps those semantic states to Tk's native `readonly` or `disabled` Entry states. Temporary capture bindings continue to return `"break"` and update the `StringVar` programmatically.

**Tech Stack:** Python 3, Tkinter, pytest, existing fake Tk widgets in `tests/test_settings_window.py`.

---

## Chunk 1: Regression tests for display-only shortcut input

**Files:**
- Modify: `tests/test_settings_window.py` around `FakeTkWidget`, `dispatch_child_event`, and existing SettingsWindow recorder tests.
- Reference: `docs/superpowers/specs/2026-09-05-readonly-keybind-display-design.md`.

- [ ] **Step 1: Run the existing settings-window tests before changing test infrastructure**

Capture the repository baseline before making any implementation edits:

```powershell
git rev-parse HEAD
git status --short
git diff --name-only
```

Record the commit, status entries, and changed paths in the task notes. Preserve those pre-existing paths throughout the work.

Run:

```powershell
py -m pytest tests/test_settings_window.py -v
```

Expected: the current settings-window test file passes. This establishes a clean baseline before adding readonly assertions.

- [ ] **Step 2: Extend the fake Entry enough to model native readonly insertion rules**

Update `FakeTkWidget` so it retains the `textvariable` passed to the fake Entry and can simulate the default Tk class binding for a key press. Define the fake's default insertion algorithm explicitly: for `<KeyPress>`, if `configured.get("state", "normal") == "normal"`, read `event.keysym`; when it is exactly one character, append that character to the current `textvariable` value with `StringVar.set()`, and otherwise do nothing. The simulation must do nothing for `"readonly"` and `"disabled"`. Keep the current custom binding dispatch first, so a callback returning `"break"` stops both parent dispatch and default insertion. The fake must still allow `FakeTkVariable.set()` to update the displayed value directly.

Update `dispatch_child_event(child, parent, sequence, event)` to run the existing child and parent callbacks, then invoke the fake default class-binding simulation only if no callback returned `"break"`. Do not add a production-only hook to support the fake.

- [ ] **Step 3: Write the failing readonly-state and direct-input tests**

Add focused tests with clear behavior names:

```python
def test_settings_window_shortcut_entry_is_readonly_when_unlocked(monkeypatch):
    window = make_settings_window(monkeypatch)

    assert window.hotkey_entry.configured["state"] == "readonly"

    window._record()
    assert window.hotkey_entry.configured["state"] == "readonly"

    window._on_key_press(FakeTkEvent(0x11, keysym="Control_L"))
    assert window.hotkey_var.get() == "Ctrl"
    assert window.hotkey_entry.configured["state"] == "readonly"

    window._on_key_press(FakeTkEvent(0x4B, keysym="k"))
    assert window.hotkey_var.get() == "Ctrl+K"
    assert window.hotkey_entry.configured["state"] == "readonly"

    window._close()
    assert window.hotkey_entry.configured["state"] == "readonly"


def test_settings_window_shortcut_entry_is_disabled_when_locked(monkeypatch):
    window = make_settings_window(monkeypatch, coordinator=make_coordinator([], locked=True))

    assert window.hotkey_entry.configured["state"] == "disabled"


def test_settings_window_readonly_entry_rejects_direct_text_but_accepts_programmatic_updates(
    monkeypatch,
):
    window = make_settings_window(monkeypatch)

    assert dispatch_child_event(
        window.hotkey_entry,
        window.window,
        "<KeyPress>",
        FakeTkEvent(0x4B, keysym="k"),
    ) is None
    assert window.hotkey_var.get() == "F24"

    window.hotkey_var.set("Ctrl+Shift+K")
    assert window.hotkey_var.get() == "Ctrl+Shift+K"


def test_settings_window_save_synchronizes_programmatic_display_value(monkeypatch):
    window = make_settings_window(monkeypatch)

    window.hotkey_var.set("K")
    window._save()

    assert window.view.hotkey_text == "K"
    assert window.view.coordinator.current.toggle_hotkey == "K"
```

The first test must assert the Entry is readonly initially, after `_record()`, after a partial key press, after completion, and after `_close()`. Extend existing tests so completion, cancel, focus-loss, close, binding-failure, lock-transition, and routed-error cleanup each assert `"readonly"` when unlocked and `"disabled"` when locked. Add a save synchronization assertion that a programmatic `hotkey_var.set("K")` is read by `_save()` and persists `K`, while no direct Entry insertion changes the value. Keep existing assertions that press/release handlers return `"break"` and that canonical combinations are shown.

- [ ] **Step 4: Run only the new tests to verify RED**

Run:

```powershell
py -m pytest tests/test_settings_window.py -k "shortcut_entry_is_readonly or shortcut_entry_is_disabled or readonly_entry_rejects_direct_text or save_synchronizes_programmatic_display_value" -v
```

Expected: the readonly-state tests fail because the unlocked Entry is currently configured as `"normal"`, and the direct-input test's fake default binding appends `k` to the displayed `F24` value. The save-synchronization test is expected to remain green before the production change. Confirm failures are assertion mismatches caused by the missing readonly state, not by incomplete dispatch infrastructure.

- [ ] **Step 5: Commit the regression tests**

```powershell
git add -- tests/test_settings_window.py
git commit -m "test: cover readonly shortcut display"
```

## Chunk 2: Map the shortcut Entry to Tk readonly state

**Files:**
- Modify: `settings_window.py:643-654` in `SettingsWindow._sync_controls()`.
- Test: `tests/test_settings_window.py` tests added in Chunk 1 and existing recorder/cleanup tests.

- [ ] **Step 1: Implement the minimal production change**

Keep `SettingsViewModel.hotkey_enabled` unchanged as the semantic unlocked/locked flag. In `_sync_controls()`, configure the Entry with:

```python
state="disabled" if self.view.locked else "readonly"
```

Do not change the recorder event bindings, view-model state transitions, or `_save()` synchronization. Tk's native readonly state blocks class-level insertion, while the existing custom capture bindings and programmatic `StringVar.set()` calls continue to work.

- [ ] **Step 2: Run the focused tests to verify GREEN**

Run:

```powershell
py -m pytest tests/test_settings_window.py -k "shortcut_entry_is_readonly or shortcut_entry_is_disabled or readonly_entry_rejects_direct_text or settings_window_handlers_return_break_and_synchronize or settings_window_cleanup or settings_window_focus_loss_cancels_active_recording or settings_window_close_discards_unsaved_candidate" -v
```

Expected: all selected tests pass, including live preview, `"break"` suppression, cancellation, completion, and readonly/disabled state assertions.

- [ ] **Step 3: Run the complete settings-window regression file**

Run:

```powershell
py -m pytest tests/test_settings_window.py -v
```

Expected: every settings-window test passes with no warnings or errors.

- [ ] **Step 4: Review the diff for scope and whitespace**

Run:

```powershell
git diff --check
git diff -- settings_window.py tests/test_settings_window.py
```

Confirm the only production behavior change is the Entry state mapping and the only test changes are fake-Tk observability plus readonly/direct-input regression coverage.

- [ ] **Step 5: Commit the implementation**

```powershell
git add -- settings_window.py tests/test_settings_window.py
git commit -m "fix: make shortcut field readonly"
```

## Chunk 3: Full verification

**Files:** None expected beyond the implementation and tests above.

- [ ] **Step 1: Run the complete test suite**

```powershell
py -m pytest -v
```

Expected: all tests pass with zero failures.

- [ ] **Step 2: Compile production and test modules**

```powershell
py -m compileall -q hotkeys.py keyboard_hook.py settings.py controller.py tray.py settings_window.py main.py build.py tests
```

Expected: exit code 0 and no output.

- [ ] **Step 3: Run repository hygiene checks**

```powershell
git diff --check
git show --check --stat HEAD
git status --short --branch
```

Expected: `git diff --check` is silent for any remaining working-tree edits, `git show --check --stat HEAD` reports the latest implementation commit with no whitespace errors, and `git status --short --branch` contains only the baseline paths captured in Chunk 1 plus no unexpected changes outside the documented implementation paths. Preserve any baseline changes; do not reset or discard them. If the status is ambiguous, inspect the path-level diff against the captured baseline before claiming the implementation is clean.

- [ ] **Step 4: Inspect Node process state if any Node-based tooling was run**

If the session ran Node-based tooling, follow `process-hygiene` and run these exact conservative checks:

```powershell
Get-Process node | Select-Object Id, ProcessName, CPU, WS, StartTime | Sort-Object WS -Descending
& 'C:\Users\Robbie Pineda\.codex\skills\process-hygiene\scripts\cleanup-orphaned-dev-processes.ps1' -WhatIf
```

Review the preview; if it lists only obvious orphaned Vitest, Playwright, esbuild, Jest, or Vite-node workers, run the same script without `-WhatIf`. If provenance or intent is ambiguous, show the candidate processes and leave them running for user review. Do not use `-IncludeDevServers` unless the user explicitly requests dev-server cleanup. This repository's planned verification uses Python only, so no Node cleanup is expected unless an auxiliary tool is introduced.
