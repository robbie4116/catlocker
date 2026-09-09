# Tester feedback: shortcut modes and single instance

Date: 2026-09-09
Status: Approved by the user; implementation is reserved for a separate session.
Implementation belongs in a separate session. This document makes no application changes.

## Intent and decisions

Address Alfie's September 6 feedback: make separate lock/unlock bindings optional and prevent multiple running copies. Preserve Robbie's Stream Deck workflow.

Confirmed: new users start in single-shortcut mode. Existing configurations retain their behavior. The user approved the detailed behavior below.

These are two independently implementable changes. Prepare separate implementation plans after this specification is reviewed.

## 1. Shortcut modes

Settings contains a checkbox labeled **Use separate lock and unlock shortcuts**.

- Unchecked: show one read-only recorder field labeled **Lock / unlock shortcut**.
- Checked: show the existing **Lock shortcut** and **Unlock shortcut** recorder fields.
- Default for new users: unchecked, retaining the current F24 shortcut default.
- Single mode toggles once per activation. Holding a shortcut must not repeatedly toggle.
- Separate mode retains existing state-dependent lock and unlock behavior, including allowing equal bindings to toggle.
- Preserve the stored toggle binding and the stored separate pair across mode switches and restarts.
- Checkbox and recorder changes are drafts until Save. Cancel or closing Settings discards drafts.
- Disable mode switching during recording; disable settings changes while locked, following existing behavior.
- Keep existing shortcut warnings, side-specific modifiers, native recording, and emergency recovery behavior.

### Configuration and migration

Persist an explicit mode flag (`separate_shortcuts`), a toggle binding (`toggle_hotkey`), the separate pair (`lock_hotkey`, `unlock_hotkey`), and existing notification preferences. Mode is explicit rather than inferred on every launch, so remembered inactive bindings do not change the selected mode.

For files without a mode flag:

1. A legacy toggle-only configuration becomes single mode, with its binding also seeding both separate fields.
2. A pair with equal canonical bindings becomes single mode and uses that binding for toggle.
3. A pair with different canonical bindings becomes separate mode; seed the remembered toggle binding from its lock binding.
4. A fresh configuration uses F24 for all three bindings and single mode.

Resolve partial or mixed files deterministically: parse each shortcut independently; a missing or invalid shortcut is unavailable. Resolve the lock binding from valid `lock_hotkey`, then valid `toggle_hotkey`, then F24. Resolve unlock from valid `unlock_hotkey`, then valid `toggle_hotkey`, then F24. When a valid Boolean mode flag is present, resolve toggle from valid `toggle_hotkey`, then the resolved lock binding, and honor the explicit mode. Without a valid Boolean mode flag, seed toggle from the resolved lock binding and infer mode from whether the resolved pair differs; this preserves the active binding of older mixed files. Thus an explicit single mode with only a toggle value seeds both pair members from toggle; an explicit separate mode with one missing member keeps separate mode and uses the stated fallback for that member.

Maintain current tolerant handling of invalid individual values without treating the entire file as corrupt. Malformed TOML or invalid text encoding follows the existing backup-and-default recovery path. Migration alone does not rewrite an otherwise readable file; Save writes resolved values. Preserve generic versus side-specific binding semantics.

Saving validates all stored bindings and installs only the active pair. Single mode produces `ShortcutPair(toggle, toggle)`; separate mode produces `ShortcutPair(lock, unlock)`. Centralize this selection so application startup and settings Save cannot disagree. Preserve the existing coordinated persistence/runtime failure handling; never show a failed save as successful or retain an unreported runtime/config mismatch.

### Boundaries

- `settings.py`: persisted fields, validation, migration, active shortcut selection. Update the current compatibility alias for `toggle_hotkey`, which presently returns the lock binding.
- `settings_window.py`: checkbox, visible recorder rows, drafts for both modes, Save/Cancel integration.
- `main.py`: construct the hook from the active selection.
- `hotkeys.py` and `keyboard_hook.py`: reuse existing shared-pair toggle behavior; avoid an engine rewrite.

### Acceptance checks

- Fresh settings display one F24 binding and an unchecked checkbox.
- Existing distinct Stream Deck bindings migrate unchanged and activate separate mode.
- Existing equal pairs and legacy toggle-only files migrate to single mode without changing their shortcut.
- Partial pairs, mixed legacy/new fields, invalid individual values, and missing or invalid mode flags follow the explicit precedence above; malformed files retain existing backup recovery.
- Save/restart and repeated mode switches preserve all remembered bindings.
- Cancel does not alter runtime bindings, stored bindings, or mode.
- Saving failure follows existing recovery behavior and displays an error.
- Trigger hold/repeat, standalone modifier release, recording cancellation, and emergency unlock still work in both modes.

## 2. One running instance

Allow one CatLocker instance per Windows user session, regardless of executable directory or installed/portable mode. Separate Windows sessions remain independent. The first instance retains ownership of its configuration; launching another copy must not replace it or import another configuration.

Acquire an operating-system-backed exclusive instance guard before creating Settings, loading/writing configuration, or starting the hook/tray. Its identity must not depend on executable path. Hold ownership through application shutdown, including startup failure cleanup. Simultaneous launches must produce exactly one owner. Process exit or a crash must not leave a stale filesystem lock that prevents restarting.

### Reopening behavior

- A normal second launch requests activation of the existing instance, then exits.
- When unlocked, the owner opens or raises its existing Settings window without duplicating it or discarding unsaved edits.
- When locked, the owner indicates **CatLocker is already running; the keyboard is locked.** Settings remains unavailable and lock state remains unchanged.
- A duplicate `--startup` launch exits quietly without activation or notification.
- If the owner is still starting, use a bounded activation wait/retry. If activation cannot be delivered, the second launch reports that CatLocker is already running and directs the user to its tray icon. It never starts another hook as a fallback.
- If instance ownership cannot be established due to an unexpected OS error, report the error and exit without creating a hook.

### Boundaries

Create `single_instance.py` for ownership and a narrow activation signal with injectable OS adapters for tests. Implement Windows guard and activation using documented Windows primitives; verify exact APIs and session/elevation behavior during implementation planning. The activation channel carries only a request to show the existing app, never lock commands, configuration, or arbitrary payloads.

`main.py` owns acquisition/release and argument handling. Dispatch activation into the existing main-thread event queue; do not manipulate Tk from a listener thread. Stop any listener during normal shutdown and startup failure. Use the existing Settings window and tray presentation paths where suitable. The locked activation response must remain visible even if optional lock/unlock notifications are disabled.

### Acceptance checks

- Repeated and simultaneous launches result in one hook and one tray icon.
- Reopening while unlocked raises Settings and preserves unsaved drafts.
- Reopening while locked reports existing status and never unlocks or toggles.
- Duplicate login startup is quiet.
- Installed and portable copies in the same session cannot both run; first owner's configuration is retained.
- Normal exit, startup failure, and forced process termination permit a subsequent fresh launch.
- Test activation timeout, inaccessible activation endpoint, and ownership errors without allowing a second owner.
- Physically verify same-session launches across different elevation levels; inability to activate must not bypass exclusivity.

## Implementation handoff

Repository baseline examined: `be906a7` (Split keyboard lock and unlock shortcuts).

Plan shortcut modes first, then single-instance behavior, as separate changes. Extend `tests/test_settings.py`, `tests/test_settings_window.py`, `tests/test_split_shortcuts.py`, and `tests/test_main.py`; add `tests/test_single_instance.py`. Keep native lifecycle tests behind injectable adapters and complete physical Windows checks for actual hooks, tray behavior, process crashes, and elevation.

For each change, run focused tests and then `py -m pytest -v`. Update `README.md` and `docs/windows-manual-test-checklist.md`. Build verification belongs to the implementation session; no tests or builds were run for this planning document.

Out of scope: changing F24 defaults, redesigning the full Settings window, adding Stream Deck integration, adding remote control, or enforcing a machine-wide lock across separate user sessions.
