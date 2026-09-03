# CatLocker Native Windows Design

## Summary

CatLocker will become a Windows 11-only, tray-first utility that blocks keyboard input while leaving the mouse and Stream Deck usable. It will replace Keylock's competing `pynput` listeners with one low-level Windows keyboard hook implemented through Python `ctypes`.

The configured shortcut is a true lock/unlock toggle. `F24` is the default. The independent `Left Ctrl + Right Ctrl` chord always forces the application into the unlocked state, and the tray always offers a mouse-accessible Unlock command.

This work preserves the upstream license and attribution. It does not add process hiding, hook concealment, anti-cheat evasion, driver code, injection, or game-specific behavior.

## Goals

- Run one `WH_KEYBOARD_LL` hook for the process lifetime.
- Maintain one authoritative lock state.
- Detect recovery and toggle shortcuts before deciding whether to suppress an event.
- Suppress ordinary keyboard input while locked without intercepting mouse input.
- Remain recoverable during autorepeat, rollover, chaotic simultaneous input, and state changes with keys held.
- Run from the Windows notification area with no persistent application window.
- Store validated per-user settings and optionally start at login, always unlocked.
- Keep the runtime implementation small, deterministic, auditable, and free of third-party hook and tray dependencies.

## Non-goals

- Cross-platform support.
- Intercepting Ctrl+Alt+Delete or any other secure Windows input path.
- Driver-level or raw-HID interception.
- Guaranteeing interception of proprietary consumer/HID keys that Windows does not expose to `WH_KEYBOARD_LL`.
- Anti-cheat compatibility guarantees or evasion.
- Cloud services, telemetry, network access, or an update checker.
- Mouse suppression.
- A major visual redesign.

## Existing Architecture and Problems

The fork currently has an always-visible Tk window. `core.py` stores module-level keyboard, mouse, shortcut, and pressed-key state. It starts a fully suppressing `pynput` listener independently from a second shortcut listener. The shortcut is an emergency unlock only, not a true toggle. Mouse and keyboard recovery are coupled.

This layout is fragile because a fully suppressing listener can prevent the competing shortcut listener from observing the same input. Shortcut parsing handles only a small set of string forms, and state is spread across several globals. The Tk code also starts a background thread that calls Tk scheduling APIs, even though Tk operations should stay on the Tk thread.

The current requirements pin `pynput==1.7.7` and `six==1.16.0`. PyPI lists `pynput` 1.8.2 as the current stable release as of this design, while the project's Windows documentation exposes selective suppression through a backend-specific event filter. CatLocker will not upgrade to that version because the selected architecture removes `pynput` and `six` entirely instead of coordinating multiple library listeners.

## Selected Approach

Use a focused native refactor with isolated modules for the pure input state machine, low-level hook, controller, tray, settings, and settings window.

Two alternatives were rejected:

- Retrofitting only the current `core.py` would preserve tangled state and the always-open GUI, leaving reliability and testability concerns unresolved.
- Splitting the engine and UI into separate processes would add IPC and lifecycle failure modes that a personal utility does not need.

## Component Boundaries

### `hotkeys.py`

Owns platform-independent shortcut data and event decisions:

- Named Windows VK constants used by the application.
- Immutable normalized shortcut representation.
- Text parsing, display formatting, and validation.
- Generic modifier-family matching and exact left/right VK handling.
- Pressed-key, trigger-latch, passed-keydown, and suppressed-keydown tracking.
- Pure event transition function returning a suppression decision and optional state transition.

This module performs no `ctypes`, registry, filesystem, GUI, or thread work and is the primary unit-test target.

### `keyboard_hook.py`

Owns the Windows hook adapter:

- A dedicated thread and its Windows message loop.
- `SetWindowsHookExW(WH_KEYBOARD_LL, ...)` and `UnhookWindowsHookEx`.
- Correct `ctypes` declarations for `KBDLLHOOKSTRUCT`, callback types, pointer-sized return values, and Win32 functions.
- A strong reference to the callback object for the full hook lifetime.
- Translation of `WM_KEYDOWN`, `WM_KEYUP`, `WM_SYSKEYDOWN`, and `WM_SYSKEYUP` into pure input events.
- Private application thread messages for lock, unlock, toggle, shortcut replacement, enter/exit recording mode, terminal fail-open, and shutdown requests.
- `CallNextHookEx` for events the state machine elects to pass.

The hook callback may update only in-memory state and enqueue a lightweight state/error notification. It must not do file I/O, logging, GUI work, network work, sleeps, waits, or blocking cross-thread calls.

### `controller.py`

Provides the application's public control surface while keeping all state changes serialized on the hook thread:

- `request_lock()`, `request_unlock()`, and `request_toggle()` post commands to the hook thread.
- `replace_shortcut()` validates before posting a replacement command and can receive acknowledgement outside the hook callback.
- `locked` exposes the authoritative state snapshot maintained by the state machine.
- State changes publish small immutable notifications to UI/tray queues.
- Startup and shutdown coordinate readiness and acknowledgements outside the hook callback.

There is exactly one `locked` value. Keyboard-triggered and posted commands mutate that same value on the hook thread.

### `tray.py`

Owns native notification-area integration:

- A dedicated hidden Win32 window and message loop.
- `Shell_NotifyIconW` add, modify, and delete operations.
- Lock-state tooltip and icon updates.
- A popup menu containing Lock Keyboard, Unlock Keyboard, Toggle Cat Mode, Settings, Start with Windows, and Exit.
- Native balloon notifications when enabled.
- Forwarding commands to the controller or main UI queue without mutating lock state directly.

Lock is disabled while locked, Unlock is disabled while unlocked, and Settings is disabled while locked. The tray icon remains available while the settings window is closed.

### `settings.py`

Owns local configuration and login startup:

- Installed-mode path: `%LOCALAPPDATA%\CatLocker\config.toml`.
- Portable-mode override: `catlocker.toml` beside the executable, used only when that file already exists.
- Defaults: `F24`, start-with-Windows disabled, notifications enabled.
- Schema merging and validation for older or partial configuration.
- Atomic save using a same-directory temporary file, flush, and `os.replace`.
- Preservation or backup of an unreadable configuration before defaults are written.
- The current-user startup value at `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`.
- Correctly quoted frozen-executable or Python/script startup command construction.

The stored lock state is never persisted. Every process start begins unlocked.

### `settings_window.py`

Owns the small Tk settings UI:

- A hidden Tk root lives on the main thread.
- The window opens only from the tray.
- Shortcut recording captures events only while the recorder has focus and Cat Mode is unlocked. Entering recording mode first receives acknowledgement from the hook thread that configured-shortcut matching is temporarily suspended; emergency detection and ordinary event propagation remain active.
- A text field provides a validated fallback.
- Start-with-Windows and notifications controls.
- Validation errors, system-shortcut warnings, and persistence failures.
- Transactional save and rollback.

The settings UI never calls Tk from background threads. It drains a queue with `after()` on the Tk thread.

### `main.py`

Acts only as the composition root:

- Loads and validates configuration.
- Creates the controller, hook, tray, and hidden Tk root.
- Waits for hook/tray readiness before entering normal operation.
- Routes fatal errors to a user-visible message while remaining fail-open.
- Coordinates orderly exit.

## Shortcut Model

A configured shortcut contains zero or more modifier families and exactly one non-modifier trigger VK.

Modifier families are `Ctrl`, `Alt`, `Shift`, and `Win`. A generic family matches either left or right physical VK. Exact left/right VK identity remains in the live pressed-key set for recovery logic.

Canonical display and persistence order is:

1. `Ctrl`
2. `Alt`
3. `Shift`
4. `Win`
5. trigger

Parsing is case-insensitive and input order-independent. Examples normalize to `F24`, `Ctrl+Shift+K`, and `Ctrl+Alt+F12`.

Supported triggers include deterministic named keys, letters, digits, function keys F1 through F24, and documented media/volume VKs delivered by the low-level hook. The parser does not accept arbitrary strings or layout-dependent punctuation that cannot be mapped deterministically without additional layout handling.

## Shortcut Validation

Validation rejects:

- Empty input.
- Modifier-only combinations.
- Unknown or ambiguous names.
- Duplicate tokens.
- More than one non-modifier trigger.
- The emergency recovery combination or attempted modifier-only equivalents.
- `Ctrl+Alt+Delete`, because secure attention input cannot be intercepted by this hook.
- Any token without a deterministic supported VK mapping.

Known system shortcuts such as `Alt+Tab`, `Win+L`, `Win+R`, and `Win+Shift+S` return a warning requiring explicit user confirmation. Warnings do not bypass validation errors.

The previous shortcut remains active if parsing, validation, in-memory replacement, or persistence fails.

## Input State and Matching

The state machine tracks:

- Exact physical VKs currently down.
- Whether the configured trigger is latched until its key-up.
- Whether the emergency chord is latched until one control key is released.
- Keydowns passed to Windows and awaiting a matching key-up.
- Keydowns suppressed and awaiting a matching key-up.
- The authoritative `locked` Boolean.

Repeated keydown messages for a VK already in the down set are autorepeat and cannot activate toggle or emergency transitions again.

The first physical keydown owns the VK's exclusive release disposition until key-up: passed or suppressed, never both. Autorepeat uses a stricter rule. A repeat is passed only when the original keydown was passed and Cat Mode is still unlocked; it is suppressed after a transition to locked. A repeat whose original keydown was suppressed remains suppressed after unlocking so an application never receives repeats without the initial press. The eventual key-up still follows the original keydown disposition.

A custom shortcut matches when its trigger transitions from up to down, every required modifier family is active, and no unconfigured modifier family is active. Holding both sides of one required family still counts as that one family. Unrelated non-modifier keys do not prevent a match, which preserves recovery during chaotic input.

The emergency chord matches only when exact `VK_LCONTROL` and `VK_RCONTROL` are both down and the chord is not latched. It always requests unlocked state and never toggles.

## Hook Decision Order

For every handled keyboard event, the state machine:

1. Updates physical pressed state for the relevant edge.
2. Detects a newly completed emergency chord and forces unlocked.
3. Otherwise detects a newly completed configured shortcut and toggles.
4. Determines pass/suppress behavior using the current lock state and down/up bookkeeping.
5. Updates latches and removes released-key bookkeeping.

The event completing either shortcut is suppressed. The configured shortcut therefore does not deliver its trigger to the foreground application.

## Transition and Key-up Invariants

Each keydown establishes the disposition of its eventual matching key-up:

- A passed keydown produces a passed key-up, even if Cat Mode locks before release.
- A suppressed keydown produces a suppressed key-up, even if Cat Mode unlocks before release.
- The event that activates toggle or emergency recovery is suppressed, and its release is also suppressed.

This prevents logically stuck modifiers without injecting synthetic input.

For an unlocked `Ctrl+Shift+K` activation, Ctrl and Shift may already have reached the foreground application before K completes the chord. K is suppressed and Cat Mode locks. The later Ctrl and Shift releases are passed so the foreground application cannot retain stuck modifier state.

For the same activation while locked, Ctrl, Shift, and K keydowns are suppressed. The chord unlocks on K, but all three releases remain suppressed so the foreground application does not receive release events without matching presses.

For `F24`, both press and release are suppressed around either transition.

The state machine does not blindly clear held-key state during a transition. It retains disposition until physical key-up so transitions cannot create unmatched events. A defensive reset is reserved for hook restart or explicit engine reinitialization, neither of which occurs during an ordinary toggle.

Replacing a shortcut while unlocked also preserves existing key dispositions. If the new trigger is already physically down, it starts latched and cannot activate until its physical release. Releasing a held old trigger clears only its existing disposition; it cannot activate either shortcut.

## Threading and Data Flow

The process uses three event-loop contexts:

- Main thread: hidden Tk root and settings UI.
- Hook thread: low-level hook, hook callback, command messages, and authoritative input state.
- Tray thread: hidden tray window and native tray message handling.

Keyboard transitions occur synchronously on the hook thread so the callback can return the correct suppression value immediately. Tray/UI commands are posted to the hook thread. Hook state notifications are enqueued for the main and tray consumers.

No hook callback waits for another thread. Operations requiring acknowledgement, such as settings replacement or shutdown, originate outside the callback and may wait with a bounded timeout while the hook thread handles the posted message.

Commands that require acknowledgement carry unique IDs. If a shortcut replacement or rollback acknowledgement times out, the application treats the engine as unhealthy, leaves the persisted configuration unchanged, enters fail-open mode, and performs coordinated shutdown. It does not continue running with an uncertain shortcut. State notifications use an unbounded `queue.SimpleQueue`; the callback never waits for a consumer.

The `LLKHF_INJECTED` flag is retained for diagnostics but does not disqualify an event. Injected events, including a Stream Deck-generated F24, use the same matching and suppression path as physical events. CatLocker does not inject replacement keyboard input.

### Recording Mode

The recorder cannot simply rely on Tk while the permanent hook recognizes the same shortcut. Before recording begins, the main thread posts an `ENTER_RECORDING` command and waits for acknowledgement. The command is accepted only while unlocked. In recording mode, the hook temporarily skips configured-toggle matching so the focused Tk control receives the candidate keystrokes normally. Exact emergency recovery detection remains active, and the application remains unlocked.

Cancel, save, recorder focus loss, settings-window close, or any tray lock/toggle request exits recording mode. A lock/toggle request exits recording mode on the hook thread before changing lock state. The settings UI then cancels its partial capture. If entry or exit acknowledgement times out, the application follows the same unhealthy-engine fail-open shutdown path rather than guessing which mode is active.

## Settings Update Transaction

Hotkey editing is disabled while locked. Saving performs:

1. Recheck that Cat Mode is unlocked.
2. Parse and validate the candidate.
3. Ask for explicit confirmation if warnings exist.
4. Post an in-memory shortcut replacement and receive acknowledgement.
5. Atomically persist all settings.
6. If persistence succeeds, apply the new live notification preference.
7. If persistence fails, restore the previous in-memory shortcut, leave the live notification preference unchanged, and show the error.

The replacement acknowledgement includes its command ID and resulting shortcut generation. A late or mismatched acknowledgement is ignored. If replacement or rollback times out, the process fails open and shuts down with the previous TOML still on disk, so no uncertain runtime state remains active.

Because the hook is permanent and shortcut matching is data-driven, changing the shortcut does not install, remove, or race a second hook.

Start-with-Windows is not duplicated in TOML. Its checkbox reads the actual named Run-key value and applies that registry change as an independent immediate action. On registry failure, the checkbox is refreshed from the actual value and the shortcut/notification configuration is untouched. The TOML transaction therefore covers only the shortcut and notification preference and cannot partially disagree with the Run key.

## Startup Behavior

The Start with Windows control writes or removes a named value below the current user's Run key. It never requests elevation and never writes machine-wide startup configuration.

Whether launched manually, from the Run key, installed, or portable, CatLocker begins unlocked and minimized to the tray. Configuration cannot request a locked startup.

## Failure Handling

- Hook installation failure: remain unlocked, display a clear error, remove any tray icon, and exit.
- Tray installation failure: unlock and stop the hook before reporting and exiting.
- Callback exception: catch it at the `ctypes` boundary, fail open by calling `CallNextHookEx`, mark the state unlocked, and enqueue a fatal error. Never allow a Python exception to escape the callback.
- Command acknowledgement timeout: persisted settings remain unchanged, but runtime state is treated as uncertain; enter terminal fail-open mode, report the error, and shut down.
- Configuration corruption: preserve the unreadable file for diagnosis and load validated defaults.
- Startup registry failure: keep the previous checkbox/value state and show the Windows error.
- Notification failure: ignore it after updating actual lock state; notifications are cosmetic.

Windows may remove a low-level hook if its callback exceeds the system timeout. The design mitigates this by keeping the callback bounded to in-memory set operations, comparisons, queue insertion, and Win32 return calls.

The first callback exception atomically poisons the engine. A poisoned engine reports unlocked and every current or later callback unconditionally calls `CallNextHookEx` without consulting pressed-key or disposition state. The main-thread lifecycle owner then unhooks and shuts down. This terminal fail-open mode is also available to the main thread if command processing becomes unresponsive.

## Tray and User Experience

CatLocker starts with no normal window. The notification-area tooltip explicitly says either `CatLocker — Keyboard Unlocked` or `CatLocker — Keyboard Locked`.

The menu contains:

- Lock Keyboard
- Unlock Keyboard
- Toggle Cat Mode
- Settings
- Start with Windows
- Exit

Lock, Unlock, and Settings enablement reflects the last authoritative state notification. Unlock remains directly available by mouse whenever locked. Separate locked/unlocked icons are desirable if legible assets are available; the tooltip and menu state remain the required unambiguous indicators.

The tray window registers and handles Explorer's `TaskbarCreated` message so it re-adds the icon after the Windows shell restarts.

Optional native tray notifications say `Cat Mode ON — Keyboard Locked` and `Cat Mode OFF — Keyboard Unlocked`.

## Shutdown

Tray Exit only enqueues an exit request and returns from the tray window procedure. The Tk/main thread is the lifecycle owner and performs this ordered sequence, so it never asks the tray thread to join itself:

1. Post force-unlock and receive acknowledgement.
2. Ask the hook thread to remove `WH_KEYBOARD_LL` with `UnhookWindowsHookEx`.
3. stop and join the hook/message-loop thread.
4. delete the notification-area icon and stop/join the tray thread.
5. destroy the Tk root and exit.

Unhooking also restores normal propagation at the operating-system level. The application never intentionally terminates while its hook remains installed and suppressing.

Acknowledgements and thread joins use bounded waits. On hook-thread timeout, the lifecycle owner first sets the shared terminal fail-open flag, then makes a best-effort direct `UnhookWindowsHookEx` call using the retained hook handle and posts `WM_QUIT` again. Tray-thread timeout cannot re-enable suppression because the hook has already been removed; shutdown proceeds after a best-effort `Shell_NotifyIconW(NIM_DELETE)`.

## Testing Strategy

Automated tests avoid requiring a live global hook wherever possible.

Pure shortcut tests cover:

- Parsing and canonical formatting.
- Case and token-order normalization.
- F13 through F24 and media/volume VK mapping.
- Generic modifier-family and exact left/right behavior.
- All validation errors and warning-only system shortcuts.
- Emergency/configured-shortcut conflict detection.

Pure state-machine tests cover:

- True toggle from both states.
- Emergency always unlocking and never locking.
- Autorepeat activating only once per physical press.
- Release-before-reactivation latching.
- Passed and suppressed down/up pairing across transitions.
- Passed keydown, transition to locked, autorepeat suppression, and passed key-up.
- Suppressed keydown, transition to unlocked, continued autorepeat suppression, and suppressed key-up.
- Modifiers held before locking and while unlocking.
- Extra modifier families rejecting configured shortcut matches.
- Unrelated non-modifier noise not blocking recovery.
- Shortcut replacement while the old or new trigger is held.
- Recording mode suspending only configured-toggle matching.
- Injected F24 following the normal toggle path.
- Many simultaneous keys, unusual release order, and late key-ups.
- System keydown/up messages using the same transition path.

Settings tests cover:

- Installed and portable path selection.
- Default/partial/corrupt configuration handling.
- Atomic save and rollback behavior.
- Correctly quoted startup command construction.
- Registry read/write failures refreshing from actual state without altering TOML.

Controller and lifecycle tests cover:

- Replacement and rollback acknowledgement timeouts entering terminal fail-open shutdown.
- Recording-mode entry/exit timeouts entering terminal fail-open shutdown.
- Poisoned-engine callbacks unconditionally passing subsequent input.
- Bounded hook/tray shutdown timeouts taking their documented fallback paths.

Windows adapter tests cover structure sizes/signatures and event translation where stable, with the actual system integration verified manually.

The Windows manual checklist includes:

- F24 lock/unlock from a Stream Deck or equivalent sender.
- Configured multi-key toggle in Notepad with no trigger leakage or stuck modifiers.
- Left Ctrl + Right Ctrl recovery while locked and unlocked.
- Tray Unlock with the mouse.
- Autorepeat and many simultaneous held keys.
- Keys held across both state transitions.
- Function, Windows, media, and volume keys exposed by the hook.
- Confirmation that mouse input is never intercepted.
- Login startup always beginning unlocked.
- Settings disabled while locked and transactional shortcut changes while unlocked.
- Exit while locked restoring input and removing both tray and hook cleanly.
- Confirmation that Ctrl+Alt+Delete remains outside application control.

## Packaging and Repository Cleanup

- Remove `pynput` and `six` from runtime requirements.
- Add only test/build tools as development dependencies if needed.
- Update PyInstaller/Inno Setup scripts for the new module layout and tray assets.
- Preserve `LICENSE`, upstream attribution, and relevant project history.
- Replace Keylock mouse-lock documentation and screenshots with CatLocker behavior and limitations.
- Do not bundle an updater, telemetry, or network permissions.

## Acceptance Criteria

- One configured shortcut toggles indefinitely without application restart.
- F24 works as the default and activates once per physical press.
- Ordinary keyboard events are suppressed while locked, subject to documented Windows hook limitations.
- Mouse input is never hooked or suppressed.
- The independent exact Left Ctrl + Right Ctrl chord always requests unlocked state.
- Tray Unlock remains mouse-accessible while locked.
- No down/up suppression mismatch caused by a transition leaves foreground applications with logically stuck modifiers.
- All lock paths use the same authoritative state.
- Settings cannot replace the shortcut while locked and cannot discard the last valid shortcut on failure.
- Automatic startup begins unlocked.
- Normal exit unlocks, unhooks, stops threads, removes the tray icon, and exits.
- Focused non-hook tests pass, and the Windows manual checklist is completed before release.

## References

- Microsoft low-level keyboard hook documentation: https://learn.microsoft.com/windows/win32/winmsg/lowlevelkeyboardproc
- Microsoft `KBDLLHOOKSTRUCT` documentation: https://learn.microsoft.com/windows/win32/api/winuser/ns-winuser-kbdllhookstruct
- Microsoft virtual-key code table: https://learn.microsoft.com/windows/win32/inputdev/virtual-key-codes
- Microsoft notification-area API: https://learn.microsoft.com/windows/win32/api/shellapi/nf-shellapi-shell_notifyiconw
- `pynput` release history: https://pypi.org/project/pynput/
- `pynput` Windows selective-suppression documentation: https://github.com/moses-palmer/pynput/blob/master/docs/faq.rst
