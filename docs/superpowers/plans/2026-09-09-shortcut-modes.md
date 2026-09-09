# Shortcut Modes Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:executing-plans to implement this plan in the separate implementation session. Steps use checkbox syntax for tracking. Do not implement during the planning session.

**Goal:** Default new users to one toggle shortcut while preserving optional separate lock/unlock bindings and all existing configurations.

**Architecture:** Persist explicit mode and three remembered bindings in AppSettings. A single active-pair selector feeds startup, Save, and rollback. Extend existing settings drafts and recorders without changing the keyboard engine's state machine.

**Tech Stack:** Python 3.11+, Tkinter, existing native keyboard hook, TOML, pytest.

**Approved spec:** `docs/superpowers/specs/2026-09-09-tester-feedback-design.md`, section 1.

**Order:** Implement before `2026-09-09-single-instance.md`. Each plan can be verified independently. Baseline application commit: `be906a7`; reconcile newer changes before editing.

## Chunk 1: Settings, presentation, and verification

### Task 1: Establish the baseline

- [ ] Read repository instructions and the approved spec. Run `git status --short`; preserve unrelated work. Use an isolated `codex/` branch/worktree if implementation needs isolation.
- [ ] Read `settings.py`, `main.py:create_application`, `settings_window.py:SettingsCoordinator`, `SettingsViewModel`, and `SettingsWindow`, plus their existing tests.
- [ ] Run `py -m pytest -v`. Record any pre-existing failures before making changes; expected result is all tests passing. Do not run the real keyboard-locking application during automated tests.

### Task 2: Add mode-aware configuration

**Modify:** `settings.py`.
**Test:** `tests/test_settings.py`, `tests/test_split_shortcuts.py`.

- [ ] Add parameterized migration tests with assertions on mode, all three bindings, active pair, and unchanged file bytes after loading readable existing files:

| Input | Mode | Toggle | Lock | Unlock |
|---|---|---|---|---|
| Fresh | single | F24 | F24 | F24 |
| Legacy toggle F12 | single | F12 | F12 | F12 |
| Equal pair F23 | single | F23 | F23 | F23 |
| Pair F23/F24 | separate | F23 | F23 | F24 |
| Equal pair F24 plus legacy toggle F12, no flag | single | F24 | F24 | F24 |
| Explicit false, toggle F12, pair F23/F24 | single | F12 | F23 | F24 |
| Explicit true, toggle F12, only lock F23 | separate | F12 | F23 | F12 |
| Invalid flag, pair F23/F24 | separate | F23 | F23 | F24 |

- [ ] Add missing/invalid individual binding tests, true/false flag type tests (integers and strings are invalid), malformed TOML and encoding backup regression tests, generic modifier preservation, and encode/load round trips retaining inactive bindings.
- [ ] Run `py -m pytest tests/test_settings.py tests/test_split_shortcuts.py -v`; confirm new expectations fail for the intended missing behavior.
- [ ] Replace the toggle compatibility property with a real stored field. Add `separate_shortcuts` Boolean. Preserve old positional toggle/notifications construction and pair-only callers: omitted mode infers from a supplied pair, while no supplied pair defaults to single. An explicit mode always wins. Keep loader precedence exactly as the spec defines; do not confuse constructor compatibility with persisted-file migration.
- [ ] Implement canonicalization and encoding of all fields. Add `AppSettings.active_shortcuts()` returning a parsed `ShortcutPair`: separate uses lock/unlock; single uses toggle/toggle. All callers use this selector rather than reproducing selection logic.
- [ ] Preserve malformed-file backup behavior, tolerant per-value fallback, atomic save, and no rewrite on readable-file migration. Update obsolete tests that assert toggle is absent from serialization; replace them with assertions that all remembered values survive.
- [ ] Rerun the focused command; expected all tests passing. Commit only this task's files with message `feat: persist shortcut modes and remembered bindings`.

### Task 3: Apply and roll back the active mode

**Modify:** `main.py`, `settings_window.py:SettingsCoordinator`.
**Test:** `tests/test_main.py`, `tests/test_settings_window.py`.

- [ ] Add tests showing startup and Save use toggle/toggle in single mode even when stored separate bindings differ, and use the distinct pair in separate mode.
- [ ] Add tests for validation of inactive fields, declined shortcut warnings, persistence failure, rollback rejection, unhealthy engine, and lock-state rejection. Assert failures do not report success, advance current settings, or silently leave the wrong active pair installed.
- [ ] Run `py -m pytest tests/test_main.py tests/test_settings_window.py -v`; confirm intended new failures.
- [ ] Extend the coordinator Save contract to accept explicit mode and toggle binding along with the existing pair and notifications. Construct a complete validated candidate; deduplicate warnings across stored shortcuts. Keep legacy callers functional through omission defaults until migrated.
- [ ] Replace startup pair construction, Save installation, and rollback installation with `active_shortcuts()` calls. Preserve current fail-open behavior on failed rollback. Assign current settings only after persistence succeeds.
- [ ] Rerun the focused command; expected pass. Commit with message `feat: apply selected shortcut mode consistently`.

### Task 4: Add the checkbox and independent recorder drafts

**Modify:** `settings_window.py:SettingsViewModel`, `SettingsWindow`.
**Test:** `tests/test_settings_window.py`, `tests/test_shortcut_recorder.py`.

- [ ] Add view-model tests for initial mode, three remembered drafts, toggling modes without losing edits, Save/restart, and Cancel/close restoring the saved mode and bindings. Test a completed recording immediately followed by a mode switch so its candidate cannot land in the wrong field.
- [ ] Add widget tests asserting exact labels, one visible row in single mode, two in separate mode, read-only fields, and disabled mode checkbox during recording or while locked.
- [ ] Run `py -m pytest tests/test_settings_window.py tests/test_shortcut_recorder.py -v`; confirm intended failures.
- [ ] Extend draft actions to `toggle`, `lock`, and `unlock`. Store draft mode independently from saved mode. Capture a completed candidate for the previous action before selecting the newly visible action. Reject mode changes during active recording or lock; do not silently cancel a live recording to switch mode.
- [ ] Add the checkbox **Use separate lock and unlock shortcuts** above recorder rows. Single mode shows **Lock / unlock shortcut** and its record button. Separate mode shows **Lock shortcut** and **Unlock shortcut** with their buttons. Put rows in frames so hidden labels/buttons leave no layout gaps. Remove the old always-visible same-shortcut instruction.
- [ ] Update control synchronization, Save, close/reset, recording feedback, and active-field focus to respect the selected mode. Avoid duplicating recorder mechanics. Keep unrelated startup and notification controls' existing behavior.
- [ ] Rerun the focused command; expected pass. Commit with message `feat: offer single or separate shortcut controls`.

### Task 5: Verify behavior and document it

**Modify:** `README.md`, `docs/windows-manual-test-checklist.md`.
**Test:** `tests/test_split_shortcuts.py`, existing hook/input tests.

- [ ] Add behavior tests using the settings selector with `InputState`: shared-trigger hold toggles only once; another press toggles back; standalone modifier toggles on release; distinct bindings remain state-dependent; emergency recovery still unlocks in both modes. Reuse existing engine test helpers.
- [ ] Run `py -m pytest tests/test_split_shortcuts.py tests/test_input_state.py tests/test_modifier_tap.py tests/test_keyboard_hook.py -v`, then `py -m pytest -v`; expected all passing.
- [ ] Update documentation for the checkbox, F24 default, preserved existing configurations, and remembered bindings. Add physical checks for Stream Deck F23/F24, native recording in each visible row, generic and side-specific modifiers, mode-switch persistence, Cancel, and emergency unlock.
- [ ] Run the app on Windows and perform those checks, with the mouse/tray recovery available. Exit the app afterward. Record physical checks that cannot be performed as pending rather than claiming success.
- [ ] Run `py build.py`; expected successful packaged build. Smoke-test its Settings controls and close the test copy. Do not publish or install over a user's running copy as part of this plan.
- [ ] Run `git diff --check`, inspect the final diff, and commit the documentation and final tests with message `test: verify shortcut modes and migration`. Report automated, build, and physical verification separately.

No Node tools are required. If implementation introduces Node-based tooling, follow the repository's process-hygiene instructions before ending.
