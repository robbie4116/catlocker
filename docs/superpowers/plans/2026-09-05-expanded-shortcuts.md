# Expanded Shortcuts Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reliably record and activate exact-side modifier combinations, standalone modifier taps, and punctuation, with clear recording errors and backward-compatible saved shortcuts.

**Architecture:** Normalize keyboard identity once in the existing Windows hook. Keep shortcut matching and recording eligibility pure, send recording events through a separate bounded session channel, and let Settings render candidates without deriving identity from Tk events. Preserve engine ownership, command acknowledgement, fail-open recovery, and saved-setting rollback.

**Tech Stack:** Python 3.11+, ctypes/Win32 WH_KEYBOARD_LL, Tkinter, TOML settings, pytest; no new runtime dependency.

**Approved spec:** `D:/Repositories/catlocker/docs/superpowers/specs/2026-09-05-expanded-shortcuts-design.md`.

**Execution boundary:** This document is the handoff to another task. Do not implement in the planning task. This plan contains contracts, test examples, and ordered work, not a prewritten application patch. Do not launch another Codex task unless the user asks for one.

**Baseline:** Spec commit `bbf4521`; application baseline `062c863`. Inspect the actual checkout before starting and preserve subsequent user changes. Paths below refer to `D:/Repositories/catlocker`; map the same repository-relative paths into the implementation worktree if one is used. All shell commands run from that checkout root.

## File responsibilities

| File | Responsibility |
|---|---|
| `D:/Repositories/catlocker/hotkeys.py` | Shortcut grammar, key catalog, canonical/display formatting, matching, InputState integration |
| `D:/Repositories/catlocker/key_identity.py` (new) | Pure native modifier normalization and immutable normalized event metadata; no Tk or ctypes calls |
| `D:/Repositories/catlocker/modifier_tap.py` (new) | Pure shared eligibility for a modifier used alone |
| `D:/Repositories/catlocker/shortcut_recorder.py` (new) | Pure recording attempts, invalid-attempt recovery, candidate and feedback state |
| `D:/Repositories/catlocker/recording_channel.py` (new) | Session envelopes, bounded event delivery, overflow/cancellation status |
| `D:/Repositories/catlocker/keyboard_hook.py` | Native adapter, owner-thread session commands, event publication, recovery |
| `D:/Repositories/catlocker/controller.py` | Acknowledged begin/finish/cancel and recording-channel access for Settings |
| `D:/Repositories/catlocker/settings_window.py` | Recorder coordination, UI polling, preview/status, candidate save, focus cleanup |
| `D:/Repositories/catlocker/settings.py` | Canonical persistence compatibility; change only if parser integration requires it |
| `D:/Repositories/catlocker/main.py` | Existing lifecycle-event pump; change only if disposal integration is needed |
| `D:/Repositories/catlocker/README.md` | User-visible shortcut rules and documented limitations |
| `D:/Repositories/catlocker/docs/windows-manual-test-checklist.md` | New dated test matrix and observed hardware results; retain prior results |

Keep new modules focused on these responsibilities. Do not refactor unrelated tray, startup, packaging, or engine lifecycle code. Existing `keyboard_hook.py` and `settings_window.py` are large; new pure behavior belongs in the focused modules above.

## Chunk 1: Shortcut semantics and engine behavior

### Task 1: Establish a baseline in the implementation task

- [ ] Read local AGENTS instructions, the approved spec, and existing `tests/test_hotkeys.py`, `tests/test_input_state.py`, `tests/test_keyboard_hook.py`, and `tests/test_settings_window.py`. Use the applicable worktree skill if isolation is needed.
- [ ] Run `git status --short` and `git log -5 --oneline`; identify existing work before edits.
- [ ] Run `py -m pytest -q`. Expected: current suite passes. Record the actual count; if it fails, investigate the failure before attributing it to this feature. Do not build an executable yet.

### Task 2: Add exact modifier grammar and stable punctuation identities

**Files:** Modify `D:/Repositories/catlocker/hotkeys.py`, `D:/Repositories/catlocker/tests/test_hotkeys.py`, and `D:/Repositories/catlocker/tests/test_settings.py`.

- [ ] Add tests for `LCtrl+K`, `RCtrl+LShift+K`, all eight standalone modifiers, both sides of one family plus a trigger, duplicates, generic standalone rejection, and generic/specific ambiguity within one family. Test generic families mixed with exact requirements in other families explicitly.
- [ ] Add matching cases: LCtrl+K accepts left only; rejects right, both Ctrl keys, and extra modifier families. Legacy Ctrl+K accepts left, right, or both; unrelated held non-modifier behavior remains unchanged.
- [ ] Add canonical/display separation tests: the display formatter returns `LCtrl + K` for an exact shortcut and `Ctrl + K` for a generic shortcut; canonical round trips have these concrete expectations:

```python
def test_side_specific_shortcut_round_trip():
    shortcut = parse_shortcut("LCtrl + K")
    assert shortcut.canonical == "LCtrl+K"
    assert parse_shortcut(shortcut.canonical) == shortcut

def test_legacy_shortcut_keeps_generic_tokens():
    assert parse_shortcut("Ctrl+Shift+K").canonical == "Ctrl+Shift+K"
```

- [ ] Add punctuation round trips for `OEM_1` (0xBA), `OEM_PLUS` (0xBB), `OEM_COMMA` (0xBC), `OEM_MINUS` (0xBD), `OEM_PERIOD` (0xBE), `OEM_2` (0xBF), `OEM_3` (0xC0), `OEM_4` (0xDB), `OEM_5` (0xDC), `OEM_6` (0xDD), `OEM_7` (0xDE), and `OEM_102` (0xE2). Verify shifted punctuation uses a Shift token and never an ambiguous literal `+` trigger.
- [ ] Add validation cases proving LCtrl+RAlt+Delete remains rejected and side-specific variants of existing system-warning shortcuts still warn. Add TOML round trips for standalone and OEM tokens and unchanged generic settings on unrelated saves.
- [ ] Run `py -m pytest tests/test_hotkeys.py tests/test_settings.py -q`; confirm new tests fail for missing behavior, not broken fixtures.
- [ ] Extend the existing Shortcut representation with exact modifier requirements and explicit activation kind, preserving legacy constructor call sites where practical. Generic family requirements and exact side requirements must remain distinguishable. Treat a standalone modifier as a release trigger; reject multiple modifier-only keys. Normalize validation to families independently of matching.
- [ ] Add a separate display formatter accepting a key-label resolver. Canonical tokens remain stable and case-insensitive to parse. Use family ordering Ctrl/Alt/Shift/Win, left before right, trigger last. Do not serialize display strings.
- [ ] Run the targeted command again. Expected: all targeted tests pass, including existing aliases and default F24 tests. Commit the task with `feat: support exact modifier and punctuation shortcut identities`, staging only the listed files.

### Task 3: Normalize native events without guessing a side

**Files:** Create `D:/Repositories/catlocker/key_identity.py` and `D:/Repositories/catlocker/tests/test_key_identity.py`; modify `D:/Repositories/catlocker/hotkeys.py`, `D:/Repositories/catlocker/keyboard_hook.py`, `D:/Repositories/catlocker/tests/test_keyboard_hook.py`, and `D:/Repositories/catlocker/tests/test_input_state.py`.

- [ ] Add a pure normalization table test for all eight explicit modifier VKs; explicit identity wins. Test generic Ctrl with its valid scan code and extended flag, generic Alt likewise, and generic Shift distinguished by its left/right scan codes. Include zero, missing, and inconsistent metadata that cannot establish a side.
- [ ] Add adapter tests for WM_KEYDOWN/UP and WM_SYSKEYDOWN/UP, retained scan code/flags, injected F24, and unknown message rejection. Keep the existing pointer-width tests.
- [ ] Run `py -m pytest tests/test_key_identity.py tests/test_keyboard_hook.py -q`; verify the new behavior fails before implementation.
- [ ] Implement immutable normalized metadata: resolved VK when available, raw VK, scan code, flags, keydown/up, and an explicit unresolved modifier-family indication. Keep existing KeyEvent construction ergonomics for pure tests. Avoid import cycles: key identity definitions have no dependency on InputState or the hook.
- [ ] Verify native mappings against official Microsoft virtual-key, scan-code, and KBDLLHOOKSTRUCT documentation before encoding them. Generic Ctrl/Alt with invalid or absent evidence remains unresolved; generic Shift requires a recognized scan code. Do not use the extended flag as the sole discriminator for Shift.
- [ ] Route both matching and future recording through this adapter. An unresolved modifier can count toward a legacy family, but cannot satisfy an exact-side shortcut or arm a standalone tap. It still counts as another held key. Preserve a consistent press/release identity; do not leave a stuck internal key after an ambiguous event. Add runtime and suppression-bookkeeping tests for resolved down followed by unresolved up, unresolved down followed by resolved up, and ambiguity while another modifier is held. If events cannot be paired safely, use existing fail-open recovery rather than guessing a physical side or retaining stuck state.
- [ ] Run the targeted tests plus `py -m pytest tests/test_input_state.py -q`. Expected: existing lifecycle and fail-open tests remain passing. Commit as `feat: preserve native modifier identity for shortcut input`.

### Task 4: Implement shared standalone-tap eligibility and release toggles

**Files:** Create `D:/Repositories/catlocker/modifier_tap.py` and `D:/Repositories/catlocker/tests/test_modifier_tap.py`; modify `D:/Repositories/catlocker/hotkeys.py` and `D:/Repositories/catlocker/tests/test_input_state.py`.

- [ ] Add shared-helper tests for a clean tap, repeats, another key already down, another key pressed and released during the hold, unmatched release, and reset while held. The helper receives ordered events plus the held set before each event; it reports eligibility without changing lock state or delivering input.
- [ ] Add engine tests for every modifier and both starting lock states. Example behavior:

```python
def test_right_alt_toggles_only_on_release():
    state = InputState(parse_shortcut("RAlt"))
    down = state.handle(KeyEvent(VK_RMENU, True))
    assert not down.changed
    assert not state.locked
    up = state.handle(KeyEvent(VK_RMENU, False))
    assert up.changed
    assert state.locked
```

- [ ] Add tests for emergency dual-Ctrl followed by releases, recording entry/exit, shortcut replacement, explicit lock/unlock commands (including no-op commands), and recovery resetting eligibility. While locked, another blocked key must still disqualify the tap.
- [ ] Add delivery assertions: a passed modifier down receives a passed release even when release locks; a suppressed down receives a suppressed release even when release unlocks. Verify no repeat toggles and keep the existing deterministic cat-mashing partition test.
- [ ] Run `py -m pytest tests/test_modifier_tap.py tests/test_input_state.py -q` and confirm the new cases fail.
- [ ] Implement the pure helper and integrate it into InputState before mutating/removing the relevant held-key state. On release, compute delivery disposition using existing bookkeeping before the lock transition. Emit `Transition.changed` and reason `toggle` only for an eligible release. Emergency unlock invalidates pending taps before returning.
- [ ] Run the targeted tests plus `py -m pytest tests/test_hotkeys.py tests/test_keyboard_hook.py -q`. Expected: new release transitions and old keydown triggers both pass. Commit as `feat: toggle standalone modifiers on an isolated release`.

## Chunk 2: Native recording and Settings integration

### Task 5: Build the pure recorder

**Files:** Create `D:/Repositories/catlocker/shortcut_recorder.py` and `D:/Repositories/catlocker/tests/test_shortcut_recorder.py`.

- [ ] Define the recorder contract in tests: initialize with a session id and pre-held keys; consume normalized ordered events; expose held preview, optional canonical candidate, explanation, and whether all keys must be released before retry. The recorder does no UI, queue, settings, or engine I/O.
- [ ] Add tests for exact-side previews, combination acceptance on trigger down, standalone acceptance on eligible up, autorepeat, initial held keys, unsupported and ambiguous keys, sticky explanation after release, and successful retry. A valid candidate becomes terminal for that recorder instance.
- [ ] Make the initial-held-key case wait for all keys to be released, including keys pressed while that wait is active; accepting a fresh attempt requires an empty held set first.
- [ ] Add a two-modifier hold followed by a valid trigger: it is a valid combination, not prematurely rejected. If all keys are released without a trigger, explain that a modifier-only shortcut must be one key. A prior invalid/ambiguous trigger keeps the attempt rejected through full release.
- [ ] Add tests proving keys from a rejected attempt cannot later become a partial shortcut and an intervening key prevents standalone acceptance even when released first. A Fn sequence producing VolumeUp records VolumeUp; absence of any event produces no invented result or timeout error.
- [ ] Run `py -m pytest tests/test_shortcut_recorder.py -q`; confirm expected failures.
- [ ] Implement the recorder using shared key formatting, parsing/validation, and modifier-tap eligibility. Do not duplicate side lookup or tap rules. Keep explanation independent of the held preview and canonical candidate.
- [ ] Run `py -m pytest tests/test_shortcut_recorder.py tests/test_modifier_tap.py tests/test_hotkeys.py -q`. Expected: all pass. Commit as `feat: model recording attempts with explicit validation feedback`.

### Task 6: Add bounded, acknowledged recording sessions

**Files:** Create `D:/Repositories/catlocker/recording_channel.py` and `D:/Repositories/catlocker/tests/test_recording_channel.py`; modify `D:/Repositories/catlocker/keyboard_hook.py`, `D:/Repositories/catlocker/controller.py`, `D:/Repositories/catlocker/tests/test_keyboard_hook.py`, and `D:/Repositories/catlocker/tests/test_controller.py`.

- [ ] Add channel tests for FIFO order, session ids, monotonically increasing sequence numbers, a capacity of 256 events, immediate overflow status, old-session rejection, and clearing old payloads. Allow a smaller injected capacity in tests.
- [ ] Add owner-thread command tests: begin acknowledges session id and immutable held-key snapshot; rejected/timeout begin cannot leave a live local recorder; cancel is session-specific and idempotent; old cancel and old finish commands cannot affect a new session. Finishing a candidate requires acknowledgement for its session and verifies the session did not overflow or become invalid before committing completion.
- [ ] Test that only active sessions publish events and lifecycle events remain independently readable. Exercise overflow occurring after a candidate event was enqueued but before the UI attempts completion: the candidate must not be accepted.
- [ ] Add tests for lock/toggle commands, stop, fail-open, and replacement invalidating sessions. Verify callbacks do not block waiting on UI consumption and unknown/ambiguous events reach recording as rejected input rather than disappearing.
- [ ] Run `py -m pytest tests/test_recording_channel.py tests/test_controller.py tests/test_keyboard_hook.py -q`; verify new tests fail.
- [ ] Implement a single-producer/single-consumer bounded channel with nonwaiting publish/read operations and an out-of-band sticky terminal/overflow status. Do not put a blocking queue operation or a UI-owned mutex wait in the callback. Document concurrency ownership, and test producer progress while consumption pauses; reject an implementation that merely uses `put_nowait` while still allowing consumer lock contention to block the hook.
- [ ] Use a separate channel object per session: the engine owns publication and terminal status, and the UI owns consumption. On termination the engine detaches that channel; the UI drops or drains its remaining payloads without clearing a newer channel. Associate overflow permanently with its session id. Document supported Python concurrency assumptions and review the publish path for consumer-held locks in addition to paused-consumer tests.
- [ ] Extend CommandResult and controller methods for recording session acknowledgements, preserving command-id validation, cancellation gates, and timeout fail-open behavior. Keep the engine as session-state owner. The finish command validates the active session's health before clearing it; completion failure restores accepted Settings state. Clearing overflow must require a new recording session.
- [ ] Publish normalized event envelopes from the hook only during active recording. Clear pending runtime taps on begin/end. Do not feed these envelopes to `main.py`'s lifecycle queue. Remove references to ended event payloads; no key logs or persistent history.
- [ ] Run the targeted command plus `py -m pytest tests/test_input_state.py tests/test_main.py -q`. Expected: acknowledgements, races, lifecycle dispatch, and fail-open tests all pass. Commit as `feat: stream native shortcut recording through bounded sessions`.

### Task 7: Connect Settings to native candidates and preserve focus behavior

**Files:** Modify `D:/Repositories/catlocker/settings_window.py`, `D:/Repositories/catlocker/tests/test_settings_window.py`, and `D:/Repositories/catlocker/tests/test_main.py`; modify `D:/Repositories/catlocker/main.py` only if shutdown wiring needs it.

- [ ] Update FakeController with session acknowledgements and a deterministic recording stream. Add fake Tk `after`/`after_cancel` support with explicit execution; tests must not depend on real sleeps.
- [ ] Replace tests that assert Tk is authoritative or generic Ctrl guesses Left with native-stream tests. Preserve tests for readonly fields, focus, fatal error routing, rollback, startup settings, and window reuse. Replace the old “programmatically changing display changes what Save persists” expectation: Save must persist the canonical candidate, never an arbitrary display string.
- [ ] Add UI tests for standalone release completion, side-specific combination display, sticky rejected status, Save disabled for partial attempts, saved generic display, completion acknowledgement failure, and an overflow after a candidate reaches the UI. Verify Fn hint text is visible during recording.
- [ ] Add an end-to-end overflow recovery test: cancellation stops native forwarding, restores the accepted shortcut, clears the invalid draft, and allows a new recording session to complete normally.
- [ ] Add cleanup tests for focus loss, Cancel, close/withdraw, destruction, lock changes, and fatal errors. A callback queued before cancel/reopen must not update the next session. Preserve completed candidates across ordinary focus loss and discard them on Cancel/close.
- [ ] Run `py -m pytest tests/test_settings_window.py tests/test_main.py -q`; confirm changed-contract tests fail.
- [ ] Replace coordinator `_pressed_vks` recording logic with the pure recorder and session contract. Initialize it from the acknowledged snapshot, and finish the native session successfully before exposing a savable candidate. Keep prior accepted canonical value distinct from display text and pending candidate.
- [ ] Add Settings-owned polling while recording (10 ms interval; at most 64 envelopes per callback). Check terminal/overflow status before consuming and before accepting a candidate. Store and cancel the Tk callback id; guard callbacks by session id and window state. Drain work over subsequent callbacks without starving the main loop.
- [ ] Keep temporary Tk key bindings only to prevent widget default actions while recording; they return `break` and do not infer identity or feed the recorder. Keep readonly entry focus and focus-loss cancellation. Remove `normalize_tk_event` and its generic-to-left fallback once all recording call sites are migrated.
- [ ] Format OEM labels on the UI thread using the active input layout and a Windows key-name resolver, with deterministic fallback such as `OEM key 3`. Isolate the resolver behind an injectable callable; use Backtick for OEM_3 on an identified US layout. Never perform layout lookups inside the hook, and never rewrite persisted identities when layout changes. Test US and fallback labeling, canonical stability, and shifted-key display.
- [ ] Run `py -m pytest tests/test_settings_window.py tests/test_shortcut_recorder.py tests/test_settings.py tests/test_main.py -q`. Expected: new behavior and retained Settings contracts pass. Commit as `feat: record shortcuts from native events in Settings`.

## Chunk 3: End-to-end checks and handoff evidence

### Task 8: Verify the complete workflow and document supported behavior

**Files:** Modify `D:/Repositories/catlocker/tests/test_settings_window.py`, `D:/Repositories/catlocker/tests/test_settings.py`, `D:/Repositories/catlocker/tests/test_keyboard_hook.py`, `D:/Repositories/catlocker/README.md`, and `D:/Repositories/catlocker/docs/windows-manual-test-checklist.md`.

- [ ] Add an integration scenario using the fake native hook API: record RAlt, finish on release, Save, reload settings, and activate both directions. Repeat for LCtrl+K and OEM_3. The wrong side must fail after reload; legacy Ctrl+K must retain either-side behavior.
- [ ] Add integration rejection coverage for malformed modifier identity, overflow, and persistence failure. Save failure restores the previous runtime shortcut; rollback failure follows existing fail-open behavior. Assert recording itself never toggles the configured shortcut.
- [ ] Run `py -m pytest tests/test_settings_window.py tests/test_settings.py tests/test_keyboard_hook.py -q`. If a new integration case fails, fix the underlying defect and rerun affected checks before broadening.
- [ ] Update README Shortcuts with side labels, standalone release behavior, legacy migration, OEM punctuation semantics, Fn-result behavior, and actual Alt/Win/AltGr limitations. Remove the old blanket rejection of modifier-only input; retain rejection of multi-key modifier-only chords and emergency-chord reservation.
- [ ] Add a fresh dated manual-results section without overwriting earlier observations. Include build hash, keyboard model, Windows version, active layouts, observed native behavior, and pass/fail/pending for each scenario below.
- [ ] For every standalone modifier, punctuation key, and supported Fn-result key tested, perform the full sequence: Record → Save → exit/restart CatLocker → lock → unlock. Repeat the applicable punctuation/label checks on a second keyboard layout, checking readable labels and unchanged persisted key identities. Mark each unavailable key, device, or layout case pending individually.

| Physical scenario | Expected result |
|---|---|
| Each L/R modifier recorded alone | Side-specific label; one toggle on isolated release in both lock states |
| LCtrl+K versus RCtrl+K | Only configured side activates; persisted result unchanged after restart |
| Legacy Ctrl+K | Both sides continue to work until rerecorded |
| Modifier held while another key is used | No standalone toggle, including after the other key is released |
| Backtick and remaining punctuation | Readable label; valid Save/reload; trigger identity remains stable |
| Second keyboard layout | Readable or explicit OEM fallback labels; layout changes do not rewrite saved identities; activation matches the saved Windows key identity |
| Supported Fn result / no Fn event | Resulting key recorded / recorder continues waiting with hint |
| Unknown key or ambiguous modifier | Persistent explanation; no savable candidate; retry after release |
| AltGr layout | Record actual result; no false claim that an extra-Ctrl sequence is an isolated RAlt tap |
| Alt and Windows standalone | Record any menu/Start effects; no stuck modifiers |
| Emergency dual Ctrl then release | Unlocks and stays unlocked regardless of configured standalone Ctrl |
| Focus loss, close, cancel/reopen | Saved shortcut preserved; no stale recording updates |
| Key held across lock/unlock | No unmatched release or stuck key in another application |

- [ ] Run `py -m pytest -q`. Expected: the entire suite passes; record the actual count and command. Run `git diff --check`; expected: no whitespace errors. Do not call unperformed hardware tests passed.
- [ ] On a usable Windows desktop, perform the manual matrix with the actual app and tester keyboard where available. Keep mouse/tray recovery available. If hardware/UI access is unavailable, mark those rows pending and provide exact reproduction steps for the user; do not invent evidence or broaden scope into driver work.
- [ ] Review the diff against all acceptance bullets in the spec, using the applicable code-review skill. Fix actionable findings and repeat only the affected checks, then the full suite if code changed materially.
- [ ] If Node-based tools were used during implementation, apply the process-hygiene skill and inspect/clean only confirmed orphaned processes. Ordinary Python unit tests do not require launching Node tooling.
- [ ] Commit the final tests/docs as `test: verify expanded shortcut recording and recovery`, staging only intended files. Report commits, automated results, and explicitly pending hardware cases. Do not publish or release an installer as part of this plan.

## Completion criteria

The code is ready for review when all automated requirements pass, the canonical/UI distinction is preserved end to end, recording and activation use the same normalized identities, and native hooks retain bounded execution and recovery. Hardware-dependent acceptance remains explicitly pending until observed. A successful unit suite alone does not establish universal Fn or AltGr support.

## Suggested prompt for the separate implementation task

Implement `docs/superpowers/plans/2026-09-05-expanded-shortcuts.md` using its approved spec. Preserve generic saved shortcuts, use exact modifier sides for new recordings, activate standalone modifiers only on an isolated release, and record through the existing native hook. Follow the staged tests and recovery requirements. Report automated verification and any pending physical-keyboard checks separately. Do not expand scope into driver support, input replay, or a release deployment.
