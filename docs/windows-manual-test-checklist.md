# CatLocker Windows Manual Test Checklist

Use this checklist to record manual verification on Windows 11. Mark each item and fill in the environment and results fields. If a required tool is unavailable, leave the item explicitly pending instead of guessing.

## Environment

- Date: 2026-09-04
- Windows edition: Microsoft Windows 11 Pro
- Windows build: 10.0.26200 (build 26200)
- Python version: 3.13.1
- PyInstaller version: 6.15.0
- Inno Setup version: unavailable (`ISCC.exe` not found)
- CatLocker build hash: `ad81a25`
- Executable hash: `E77778E94DAAF94E4E24D8088050FA0594901908BC3C49F89FCBB53FBAF72269`
- Tested machine: local Windows development host
- Tested input devices: not evaluated; native UI control exposed no targetable Windows applications
- Stream Deck present: pending
- Physical F24 sender present: no
- `ISCC.exe` present: no

## Results

- Overall result: Automated acceptance completed; bounded native smoke test pending.
- Notes: The fresh pinned environment passed the complete unit suite (271 tests), compileall, both diff checks, and the CatLocker-relevant PyInstaller warning assertion. The final one-file artifact was not interactively smoke-tested because the available native UI bridge returned no targetable Windows applications; no tray or Settings interaction is claimed. The bounded smoke attempt was force-cleaned and no CatLocker process remained.
- Pending items: Native tray/Settings smoke interaction, physical Stream Deck/F24 validation, visual tray-state assertions, post-exit keyboard restoration, configured-shortcut/Settings persistence matrix, Explorer restart, startup-at-login, and Inno Setup build.

## Manual Scenarios

- [ ] Launch CatLocker and confirm it appears only in the tray.
- [ ] Confirm CatLocker starts unlocked after a normal launch.
- [ ] Confirm CatLocker starts unlocked after login startup.
- [ ] Confirm the tray tooltip reports the unlocked state.
- [ ] Confirm the tray menu includes `Lock Keyboard`, `Unlock Keyboard`, `Toggle Cat Mode`, `Settings`, `Start with Windows`, and `Exit`.
- [ ] Confirm the mouse remains usable while CatLocker is running.
- [ ] Confirm `F24` locks CatLocker from a Stream Deck or equivalent sender.
- [ ] Confirm repeated `F24` presses toggle lock state exactly once per physical press.
- [ ] Confirm held `F24` autorepeat does not produce extra toggles.
- [ ] Confirm a configured shortcut such as `Ctrl+Shift+K` toggles lock state.
- [ ] Confirm the configured shortcut does not leak an extra trigger into Notepad.
- [ ] Confirm no stuck modifiers remain after locking or unlocking with a configured shortcut.
- [ ] Confirm exact `Left Ctrl + Right Ctrl` forces CatLocker unlocked while locked.
- [ ] Confirm exact `Left Ctrl + Right Ctrl` does not lock CatLocker when already unlocked.
- [ ] Confirm tray `Unlock Keyboard` works with the mouse while locked.
- [ ] Confirm tray `Unlock Keyboard` is disabled or unavailable while already unlocked.
- [ ] Confirm `Settings` is disabled while locked.
- [ ] Confirm `Settings` opens and saves only while unlocked.
- [ ] Confirm shortcut changes are validated before being saved.
- [ ] Confirm known Windows system shortcuts show the expected warning behavior.
- [ ] Confirm `Ctrl+Alt+Delete` is documented as unsupported and is not tested as an intercept case.
- [ ] Confirm function keys, including `F24`, are handled as expected by the selected sender.
- [ ] Confirm Windows keys, media keys, and volume keys behave as documented by the hook.
- [ ] Confirm many simultaneous held keys do not break recovery.
- [ ] Confirm held modifiers across lock and unlock transitions do not create stuck keys.
- [ ] Confirm keys released in an unusual order still leave applications in a clean state.
- [ ] Confirm the tray remains usable after an Explorer restart.
- [ ] Confirm notification preference changes persist correctly.
- [ ] Confirm portable mode uses `catlocker.toml` beside the executable.
- [ ] Confirm installed mode uses `%LOCALAPPDATA%\CatLocker\config.toml`.
- [ ] Confirm no telemetry, network access, or update behavior is present.
- [ ] Confirm `WH_KEYBOARD_LL` is the keyboard mechanism in use and no mouse hook is installed.
- [ ] Confirm elevated windows do not claim any special bypass or evasion behavior.
- [ ] Confirm proprietary HID / anti-cheat limitations are documented without evasion claims.
- [ ] Confirm exit while locked restores normal keyboard behavior and removes the tray icon.
- [ ] Confirm `LICENSE` and upstream Axorax/Keylock credit are present in the release docs.

## Evidence

- Screenshot or photo references:
- Log or note references:
- Open issues:

## Pending Hardware / Tools

- Physical Stream Deck available: pending
- `ISCC.exe` available: pending
- If either item is unavailable, record that the scenario could not be completed physically and why.

## Expanded Shortcut Retest — 2026-09-05

This section preserves the earlier results above and records the expanded-shortcut implementation separately.

- Build commit: final pushed `main` commit (reported with the artifact below)
- Fresh executable SHA-256: recorded with the final rebuild result
- Keyboard model: pending physical-device access
- Windows version: pending physical-device access
- Active keyboard layouts: pending physical-device access
- Automated result: complete Python suite passed; no physical keyboard or native Settings/tray interaction was performed in this task
- Overall result: pending physical verification

| Physical scenario | Result | Reproduction / evidence required |
|---|---|---|
| Each L/R modifier recorded alone | PENDING | Record → Save → restart → lock → unlock in both starting lock states; verify side label and one toggle on release |
| `LCtrl+K` versus `RCtrl+K` | PENDING | Record and save each side; restart and verify only the saved side activates |
| Legacy `Ctrl+K` | PENDING | Load an existing generic setting and verify left, right, and both Ctrl sides still activate before rerecording |
| Modifier held while another key is used | PENDING | Hold a configured standalone modifier, press/release another key, then release the modifier; verify no toggle |
| Backtick and remaining punctuation | PENDING | Record each OEM key, save/reload, and verify the persisted OEM token is unchanged; include shifted punctuation |
| Second keyboard layout | PENDING | Repeat punctuation labels and save/reload on another layout; verify labels/fallbacks and identities are stable |
| Supported Fn result / no Fn event | PENDING | Record a key that emits `VolumeUp` or another supported event; try a no-event Fn combination and verify waiting plus the hint |
| Unknown key or ambiguous modifier | PENDING | Verify the explanation remains after release, Save stays disabled, and retry works only after the attempt is released |
| AltGr layout | PENDING | Verify actual hook output; do not treat extra-Ctrl AltGr output as an isolated `RAlt` tap |
| Alt and Windows standalone | PENDING | Record both and note native menu/Start effects; verify no stuck modifiers |
| Emergency dual Ctrl then release | PENDING | While locked, press exact Left Ctrl + Right Ctrl and release both; verify unlock and no relock |
| Focus loss, close, cancel/reopen | PENDING | Verify accepted shortcut restoration, candidate retention on ordinary focus loss, and stale-session rejection after reopen |
| Key held across lock/unlock | PENDING | Hold keys through lock/unlock and test another application for unmatched releases or stuck modifiers |

No hardware result is inferred from the automated tests; every row above remains pending until exercised on the target Windows keyboard and layouts.

## Shortcut Modes Retest — 2026-09-09

This section preserves the earlier results above and records the shortcut-modes (single toggle vs. separate lock/unlock, with the **Use separate lock and unlock shortcuts** checkbox) implementation separately.

- Build commit: final pushed `main` commit (reported with the artifact below)
- Fresh executable SHA-256: recorded with the final rebuild result
- Keyboard model: pending physical-device access
- Windows version: pending physical-device access
- Stream Deck present: pending
- Automated result: complete Python suite passed, including the new `active_shortcuts()`-driven `InputState` tests in `tests/test_split_shortcuts.py`; no physical keyboard or native Settings/tray interaction was performed in this task
- Overall result: pending physical verification

| Physical scenario | Result | Reproduction / evidence required |
|---|---|---|
| Stream Deck `F23`/`F24` survive upgrade in separate mode | PENDING | On a machine already configured with distinct Stream Deck `F23` (lock) and `F24` (unlock) bindings, upgrade CatLocker; verify Settings opens with the checkbox already checked and both bindings shown unchanged, and that both physical buttons still perform their own action only |
| Native recording in each visible row | PENDING | In single mode, record a new shortcut into the one **Lock / unlock shortcut** row and verify it saves and activates; check the checkbox to enter separate mode and record distinct shortcuts into the **Lock shortcut** and **Unlock shortcut** rows and verify both save and activate independently |
| Generic vs. side-specific modifiers in both modes | PENDING | Record a generic `Ctrl+<key>` shortcut and verify either physical Ctrl key activates it; record a side-specific `LCtrl+<key>` or `RCtrl+<key>` shortcut and verify only that physical side activates it; repeat both in single mode (one row) and separate mode (both rows) |
| Mode-switch persistence across restart | PENDING | Record distinct bindings in separate mode, save, restart the app, and confirm the checkbox is still checked and both bindings are intact; uncheck the checkbox, record a new toggle shortcut, save, restart, and confirm single mode and that toggle binding persisted; reopen Settings and re-check the checkbox to confirm the earlier separate-mode pair was still remembered |
| Cancel discards unsaved changes | PENDING | Open Settings, toggle the checkbox and/or record a new shortcut in either mode, then click Cancel (or close the window without saving); verify the tray tooltip, active shortcut behavior, and `config.toml` on disk are all unchanged from before opening Settings |
| Emergency unlock in both modes | PENDING | While locked in single mode, press the exact `Left Ctrl + Right Ctrl` chord and verify it unlocks; switch to separate mode with distinct lock/unlock bindings, lock again, and verify the same chord still unlocks |

No hardware result is inferred from the automated tests; every row above remains pending until exercised on the target Windows keyboard and layouts.

## Single Instance Retest — 2026-09-09

This section preserves the earlier results above and records the single-instance (session-scoped `InstanceGuard` mutex/event ownership and activation) implementation separately.

- Build commit: `main` at the tip of the single-instance plan (see final commit reported with this checklist update)
- Fresh executable: `py build.py` succeeded; `dist\CatLocker.exe` confirmed to bundle `single_instance` (checked `build\CatLocker\PYZ-00.toc`)
- Windows version: 11 Pro (10.0.26200), the same machine this implementation session ran on
- Second Windows session or account available: not available this session
- Automated result: complete Python suite passed (597 tests, 0 skipped), including the Windows-only subprocess tests in `tests/test_single_instance.py::TestNativeInstanceRaces` described below the table.
- Non-visual physical verification performed against the real packaged `dist\CatLocker.exe` this session (process lists via `tasklist`, exit codes, and the real `%LOCALAPPDATA%\CatLocker\config.toml` file — no on-screen/GUI confirmation, see note below the table for why): repeated launch, simultaneous launch, duplicate `--startup`, and forced-termination-then-restart all behaved correctly (see per-row notes). Every check that requires seeing the screen (Settings window, the locked-instance dialog, tray icon count) or locking the real keyboard was deliberately left for a human to verify — see the note below the table.
- Overall result: partially verified by process-level evidence; full visual/GUI and locked-state verification still pending

| Physical scenario | Result | Reproduction / evidence required |
|---|---|---|
| Repeated launch of packaged build | PARTIAL | Launched `dist\CatLocker.exe`, then launched a second copy while the first ran: the second exited with code 0 within ~1s and `tasklist` showed the original two `CatLocker.exe` processes unchanged (no second hook). **Not confirmed:** that the raised window is visually the existing Settings window (requires eyes on screen) |
| Simultaneous launch | PARTIAL | Started two copies of `dist\CatLocker.exe` within the same second: `tasklist` showed the same pre-existing two-process pair afterward, with no additional `CatLocker.exe` processes — exactly one owner. **Not confirmed:** visual single tray icon |
| Locked launch | PENDING (not attempted) | Deliberately not exercised this session: triggering it requires actually locking the real keyboard on the machine this implementation session was running on, which would have interrupted the interactive terminal session doing the work. Needs a human to lock CatLocker, launch it again, and verify the exact message "CatLocker is already running; the keyboard is locked." appears, lock state is unchanged, and no second hook starts |
| Unsaved Settings drafts survive reopen | PENDING (not attempted) | Requires interacting with the real Settings window (open it, start an edit/recording, trigger a duplicate launch, confirm the draft is still there) — needs a human at the keyboard/screen |
| Duplicate `--startup` launch | CONFIRMED (process-level) | Ran `dist\CatLocker.exe --startup` while the app was already running: exited with code 0 in under a second, `tasklist` showed no new process, and no window was requested to open (activation for `--startup` duplicates is skipped entirely in code, confirmed both by this run and by `tests/test_main.py`'s `test_main_duplicate_startup_launch_exits_quietly_without_activation_or_dialog`) |
| Installed and portable configs remain untouched | PENDING (not attempted) | Only one config path (`%LOCALAPPDATA%\CatLocker\config.toml`) was exercised this session; a real installed-vs-portable pair with distinct configs was not set up |
| Owner crash and restart | CONFIRMED | Force-terminated the running `CatLocker.exe` processes via `taskkill /F`, then relaunched: the new launch acquired ownership immediately (new PIDs, no error), with no stale-lock failure. The real `config.toml` was verified byte-for-byte unchanged before and after this whole session's testing |
| Standard and elevated launches, both orders | PENDING (not attempted) | Requires launching once as a normal user and once elevated ("Run as administrator"), in both orders — not exercised this session |
| One tray icon and hook maintained throughout | PARTIAL | Process-pair count stayed constant (never more than one hook/tray process pair) across every launch scenario tried this session. **Not confirmed:** visual tray icon count |
| Startup launch stays quiet | CONFIRMED (process-level) | See "Duplicate `--startup` launch" row — exit code 0, no new process, under a second |
| Locked status visible with notifications off | PENDING (not attempted) | Requires locking CatLocker with notifications disabled — not exercised this session for the same reason as "Locked launch" above |
| Session/account independence | PENDING | No second Windows session or account was available this session |

**Why some rows were deliberately skipped rather than attempted and guessed at:** this implementation session ran interactively on the same physical machine and keyboard being used to do the work. Actually triggering Cat Mode's locked state would have intercepted that same keyboard mid-session, and confirming on-screen behavior (the Settings window, the locked-instance dialog, tray icon count) would have required a full-desktop screenshot tool that cannot be scoped to just the CatLocker window — both were judged too disruptive/risky to attempt unsupervised. Everything above marked CONFIRMED or PARTIAL was verified through `tasklist`/exit-code/file evidence against the real packaged executable, not simulated. Rows marked PENDING genuinely need a human at the keyboard and screen.
