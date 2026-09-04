# CatLocker Reliability Hardening Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Correct the safety, lifecycle, settings, startup, tray, and packaging defects found during final review of commit `709ba5bb69a51235c1dd9fe41309e00943e5d694`, then push the verified result directly to `origin/codex/catlocker-native-windows`.

**Architecture:** Preserve the existing single-hook, native-`ctypes`, Windows 11-only design. Make `InputState` hook-thread-owned so the low-level callback never contends with another thread, publish read-only state separately, and make terminal fail-open an immediately visible cross-thread gate. Route hook, tray, and settings failures into one main-thread shutdown path; keep native cleanup best-effort and bounded. Retain the current files and public concepts unless a small seam is required for testing.

**Tech Stack:** Python 3.11+, standard-library `ctypes`, Win32 `WH_KEYBOARD_LL`, native notification-area APIs, Tkinter, TOML through `tomllib`, `pytest`, PyInstaller, Inno Setup.

**Design authority:** `docs/superpowers/specs/2026-09-03-catlocker-native-windows-design.md`. This is a corrective plan for the already approved design, not a feature expansion. Do not add Windows 10 support, mouse interception, dependencies, telemetry, hiding, injection, drivers, or game-specific behavior.

---

## Chunk 1: Safety-critical hook and shutdown corrections

### Task 1: Make input state exclusively owned by the hook thread

**Files:**
- Modify: `keyboard_hook.py:72-99`
- Modify: `keyboard_hook.py:290-878`
- Modify: `controller.py:21-76`
- Test: `tests/test_keyboard_hook.py`
- Test: `tests/test_controller.py`

The callback and `WM_APP_COMMAND` dispatcher already execute on the hook owner thread. Use that fact instead of sharing `_state_lock` with UI callers.

- [ ] **Step 1: Add failing regression tests for locked callback contention**

Add `test_locked_callback_never_passes_during_concurrent_snapshot_reads`. For the initial RED characterization, acquire the existing `_state_lock` directly, invoke `_callback(HC_ACTION, ...)`, and release the gate in `finally`; this deterministically reproduces the current leak. After replacing `_state_lock`, migrate the same test setup to pause a reader inside a private `_read_published_snapshot()` seam while the callback runs. The behavioral assertions remain unchanged: a blocked/concurrent public reader must never block the callback or cause it to pass input.

```python
assert result == 1
assert VK_A in hook.state.pressed
assert VK_A in hook.state.suppressed_down
```

Also add `test_concurrent_snapshot_reads_preserve_down_up_suppression`, using the same deterministic gate and emitting the matching key-up after keydown. Verify both events are suppressed and the VK is absent from every tracking set afterward. Use barriers/events rather than probabilistic sleep loops.

- [ ] **Step 2: Add failing publication and ownership tests**

Add these exact tests:

- `test_only_hook_thread_mutates_input_state_after_startup`
- `test_locked_publication_precedes_transition_event`
- `test_locked_publication_precedes_command_acknowledgement`
- `test_terminal_fail_open_is_immediately_visible_before_owner_reconciliation`

Replace the lock-coupled expectations in `test_callback_failure_does_not_wait_for_state_lock`, `test_fail_open_serializes_paused_command_state_commit`, and `test_fail_open_serializes_paused_callback_state_transition`. The replacements must test owner-thread mutation, immediate terminal fail-open, and publication ordering without directly mutating `hook.state` from a worker thread.

- [ ] **Step 3: Run the focused tests and verify RED**

Run:

```powershell
py -m pytest tests/test_keyboard_hook.py tests/test_controller.py -k "concurrent_snapshot or hook_thread_mutates or publication_precedes or immediately_visible" -v
```

Expected: the callback-contention/down-up tests fail because input is passed while `_state_lock` is held; ownership/publication tests may also fail until the new snapshot path exists.

- [ ] **Step 4: Separate authoritative hook state from its read-only publication**

Add a frozen `HookSnapshot` value with exactly one field, `locked: bool`, and store the latest instance in `KeyboardHook._published_snapshot`. Publishing means replacing that object reference from the hook owner thread; reads copy/reference it directly without taking a lock. This project targets standard CPython, where the GIL makes the reference replacement indivisible. Do not use a lock-backed property or `threading.Event.set()` in the callback publication path.

Refactor `KeyboardHook` with these invariants:

- `InputState`, its pressed-key sets, recording state, shortcut, and lock transitions are read or mutated only by the hook owner thread after startup.
- `_callback()` never attempts `_state_lock.acquire()` and never passes an ordinary locked event merely because another thread is reading state.
- Controller/UI reads use `_published_snapshot`. It is derived from `InputState`; only the hook owner publishes it, so it is not a second writable lock authority.
- Publish after construction/startup initialization; after every callback transition; after every accepted command that can change lock/recording/shortcut presentation; and after owner-thread fail-open reconciliation. A lock transition must be published before its acknowledgement or `EngineEvent` becomes observable.
- `fail_open` remains an immediate terminal cross-thread gate. Once set, callbacks pass input and no later command may relock.
- External failure/shutdown methods set the terminal gate immediately, then request owner-thread reconciliation where possible. They must not directly race mutations of `InputState`.
- The callback remains free of file I/O, logging, dialogs, waits, sleeps, and blocking lock acquisition.

Keep `locked` as the public read interface: return `False` whenever the terminal fail-open gate is set, otherwise return `_published_snapshot.locked`. `InputState.locked` remains the sole authoritative mutable lock state.

- [ ] **Step 5: Make the outer callback exception path attempt downstream forwarding**

After terminal fail-open is requested, call `_call_next_or_zero(...)` instead of returning literal zero directly. Preserve the final zero fallback only when `CallNextHookEx` itself fails. Add a regression test for an exception raised before the normal call-next branch.

- [ ] **Step 6: Run the focused hook/controller suite and verify GREEN**

Run:

```powershell
py -m pytest tests/test_keyboard_hook.py tests/test_controller.py -v
```

Expected: all focused tests pass, including the new contention and callback-boundary cases.

- [ ] **Step 7: Commit the hook-state correction**

```powershell
git add -- keyboard_hook.py controller.py tests/test_keyboard_hook.py tests/test_controller.py
git commit -m "fix: keep keyboard state on the hook thread"
```

### Task 2: Make timeouts and emergency cleanup genuinely bounded

**Files:**
- Modify: `keyboard_hook.py:41-69`
- Modify: `keyboard_hook.py:357-503`
- Modify: `keyboard_hook.py:879-909`
- Modify: `tray.py:610-707`
- Modify: `tray.py:875-897`
- Modify: `main.py:65-109`
- Modify: `main.py:152-213`
- Test: `tests/test_keyboard_hook.py`
- Test: `tests/test_tray.py`
- Test: `tests/test_main.py`

- [ ] **Step 1: Add failing command-cancellation deadline tests**

Add `test_commit_operation_does_not_block_timeout_cancellation`. Pause a command after its commit has been claimed but before its operation returns. Assert that the submitting thread raises `HookTimeout` within a small bounded interval and does not wait for the paused operation.

The assertion should use an event plus a generous CI margin, for example:

```python
assert submitter_done.wait(timeout=0.25)
assert isinstance(submit_errors[0], HookTimeout)
```

Then release the operation and assert terminal fail-open remains externally visible, no locked snapshot or locked `EngineEvent` escapes, later relock commands are rejected, and owner-thread reconciliation leaves `InputState.locked` false. Drive the timeout through `CatModeController` so its required terminal fail-open response is exercised. Release every pause gate in `finally` so a RED test fails by assertion rather than hanging pytest.

- [ ] **Step 2: Add failing fallback-lock tests**

Add `test_shutdown_deadline_survives_stalled_hook_cleanup`, `test_shutdown_deadline_survives_stalled_tray_cleanup`, and `test_shutdown_with_zero_remaining_budget_still_destroys_root`. Create fakes that stall:

- owner-thread `UnhookWindowsHookEx` while `_hook_lock` would previously be held;
- tray icon deletion while `_presentation_lock` would previously be held;
- an emergency cleanup method itself.

Add `shutdown_timeout` to `AppLifecycle`, defaulting to `command_timeout + (4 * thread_timeout)`. `shutdown()` establishes exactly one monotonic deadline before attempting unlock. Every subsequent stage receives `min(its normal per-stage cap, max(0, deadline - monotonic()))`. With zero remaining budget, still dispatch each best-effort emergency unhook/icon-removal/quit operation on a daemon worker, join it for zero time, and continue immediately; never skip the required fallback attempt. `root.destroy()` is always executed. Assert shutdown reaches root destruction within `shutdown_timeout` plus a small scheduler margin and that no non-daemon cleanup worker remains.

- [ ] **Step 3: Run the new deadline tests and verify RED**

Run:

```powershell
py -m pytest tests/test_keyboard_hook.py tests/test_controller.py tests/test_tray.py tests/test_main.py -k "commit_operation or shutdown_deadline or zero_remaining" -v
```

Expected: at least one bounded assertion fails because cancellation or cleanup exceeds its deadline; the tests themselves must always release fake stalls and terminate.

- [ ] **Step 4: Stop holding lifecycle locks across operations**

Change `_CommandLifecycle.commit()` to claim commit under its lock, release the lock, and only then execute the operation. `cancel()` must return promptly once commit was claimed.

For hook/tray native resources, use an explicit `AVAILABLE`, `IN_PROGRESS`, `DONE` cleanup state plus a unique claim token:

- atomically claim the handle/presentation and set `IN_PROGRESS` under its lock;
- release the lock before invoking a Win32/Shell API;
- reacquire only to publish `DONE` on success or restore `AVAILABLE` on failure, and only when the same claim token still owns `IN_PROGRESS`;
- a competing normal/emergency caller that observes `IN_PROGRESS` returns immediately without issuing a duplicate native call;
- make duplicate normal/emergency cleanup idempotent;
- never wait for a lock held across a native call.

- [ ] **Step 5: Bound lifecycle fallback invocations themselves**

Add a small private lifecycle helper that invokes emergency-only cleanup on a daemon worker and joins only for `min(thread_timeout, remaining_global_budget)`. Use it for `force_unhook`, emergency icon removal, and quit fallbacks. Retry joins use the same remaining global deadline and stage cap; they do not start a fresh timeout. A stalled cleanup worker must not block the Tk/main thread or keep Python alive.

Do not use this helper for the normal owner-thread shutdown path.

- [ ] **Step 6: Run the focused deadline suite and verify GREEN**

Run:

```powershell
py -m pytest tests/test_keyboard_hook.py tests/test_controller.py tests/test_tray.py tests/test_main.py -k "timeout or deadline or cleanup or stop" -v
```

Expected: all selected tests pass without lingering non-daemon workers.

- [ ] **Step 7: Commit bounded cleanup**

```powershell
git add -- keyboard_hook.py tray.py main.py tests/test_keyboard_hook.py tests/test_tray.py tests/test_main.py
git commit -m "fix: bound fail-open cleanup paths"
```

## Chunk 2: Tray and settings lifecycle corrections

### Task 3: Preserve every partial tray update

**Files:**
- Modify: `tray.py:177-223`
- Modify: `tray.py:643-664`
- Modify: `tray.py:805-822`
- Test: `tests/test_tray.py`

- [ ] **Step 1: Add a failing burst-update test**

Add `test_burst_updates_preserve_every_partial_field`. Queue, before one drain:

```python
TrayUpdate(notifications_enabled=False)
TrayUpdate(startup_enabled=True)
TrayUpdate(locked=True, reason="toggle")
```

Assert that the final state is locked, startup is enabled, notifications are disabled, the tooltip is locked, and no balloon is shown because the final notification preference is disabled.

- [ ] **Step 2: Add failing ordering tests for multiple lock transitions**

Add `test_burst_uses_latest_lock_notification_and_avoids_redundant_modify`. From the initially unlocked presentation, queue `locked=True/reason="toggle"`, `startup_enabled=True`, then `locked=False/reason="emergency"`, with notifications enabled. Assert final unlocked/startup-enabled state, zero icon modifications because final presentation equals the initial presentation, and one unlocked emergency balloon.

Add `test_later_lock_update_replaces_earlier_custom_tooltip`. Queue `tooltip="custom"` followed by `locked=True` with no explicit tooltip. Assert the final tooltip is the standard locked tooltip rather than the stale custom value.

- [ ] **Step 3: Run the tray tests and verify RED**

```powershell
py -m pytest tests/test_tray.py -k "burst_updates or burst_uses or replaces_earlier_custom_tooltip" -v
```

Expected: `test_burst_updates_preserve_every_partial_field` fails because the first update's notification preference is lost. The custom-tooltip case is regression coverage and may already pass on the baseline.

- [ ] **Step 4: Implement field-aware draining**

Drain all queued `TrayUpdate` objects in arrival order into local state, preserving the latest provided value of each optional field. Perform at most one final `NIM_MODIFY` for the batch. Preserve the newest notification-worthy transition and decide whether to show it using the final notifications preference.

- [ ] **Step 5: Run all tray tests and verify GREEN**

```powershell
py -m pytest tests/test_tray.py -v
```

Expected: all tray tests pass, including the three new burst/coalescing cases.

- [ ] **Step 6: Commit tray update correctness**

```powershell
git add -- tray.py tests/test_tray.py
git commit -m "fix: preserve queued tray state updates"
```

### Task 4: Treat loss of the tray as a fatal recovery failure

**Files:**
- Modify: `tray.py:543-729`
- Modify: `main.py:65-282`
- Test: `tests/test_tray.py`
- Test: `tests/test_main.py`

- [ ] **Step 1: Add failing runtime tray-death tests**

Add these exact tests:

- `test_unexpected_clean_message_loop_return_publishes_fatal_event`
- `test_message_loop_exception_publishes_fatal_event`
- `test_window_proc_taskbar_restore_failure_publishes_fatal_event`
- `test_window_proc_modify_failure_publishes_fatal_event`
- `test_lifecycle_shuts_down_when_tray_runtime_fails`
- `test_post_update_rejects_an_unexpectedly_dead_tray_before_enqueue`

Prove that an exception after successful tray startup:

- publishes a fatal tray event containing the original error;
- is consumed on the Tk/main thread;
- immediately enters terminal fail-open;
- attempts unlock/unhook cleanup;
- destroys the Tk root;
- never leaves the application marked running.

An unexpected clean return from the message loop must have the same fatal behavior. Exceptions raised while handling Win32 window messages—especially `TaskbarCreated` icon restoration and `NIM_MODIFY`—must be caught at the Python `WNDPROC` boundary, converted to fatal events, and cause the tray owner loop to quit. Intentional shutdown is explicitly excluded from fatal reporting.

`post_update()` after an already-started tray thread dies must report `TrayStopped` before placing anything on `_updates`, so unreachable updates are not retained.

- [ ] **Step 2: Run the new tests and verify RED**

```powershell
py -m pytest tests/test_tray.py tests/test_main.py -k "message_loop_return or message_loop_exception_publishes or window_proc or tray_runtime or unexpectedly_dead" -v
```

Expected: the new tests fail because runtime death is not published, window-procedure failures are not contained, and dead-tray updates are currently queued silently.

- [ ] **Step 3: Publish tray lifecycle events**

Give `NativeTray` an unbounded event queue containing a small immutable fatal-event value and an explicit intentional-stop flag. After readiness, any message-loop exception or unexpected clean return must be placed on that queue before owner cleanup completes. Startup failures continue through the existing synchronous start handshake, and intentional stop/quit never publishes a fatal event.

Wrap the Python `WNDPROC` body in an exception boundary. On failure, publish the fatal event exactly once, request owner-loop quit, and return a deterministic safe result without allowing a Python exception to escape the `ctypes` callback.

Make `post_update()` distinguish “not started yet” from “started and unexpectedly dead.” The latter raises `TrayStopped` with the saved cause.

- [ ] **Step 4: Integrate tray events into the main pump**

Have `AppLifecycle.pump_events()` consume fatal tray events and route them through one idempotent helper. Use a dedicated `_fatal_handled` guard; the helper must not set `_closing` itself. It calls terminal fail-open and reports the error, then invokes `shutdown()`, which remains the sole method that atomically sets `_closing` before executing cleanup. This prevents the existing early-return guard from suppressing shutdown.

The helper:

1. marks the fatal error handled without marking shutdown complete;
2. enters terminal keyboard fail-open immediately;
3. reports the original error on the main thread;
4. invokes the bounded `shutdown()`, which sets `_closing` and performs cleanup.

Catch `TrayStopped` from presentation updates and route it through the same helper. Avoid duplicate error dialogs for the same failure.

- [ ] **Step 5: Run tray and lifecycle tests and verify GREEN**

```powershell
py -m pytest tests/test_tray.py tests/test_main.py -v
```

Expected: all tray/lifecycle tests pass, including clean-return, callback-boundary, dead-update, and coordinated-shutdown coverage.

- [ ] **Step 6: Commit tray-failure handling**

```powershell
git add -- tray.py main.py tests/test_tray.py tests/test_main.py
git commit -m "fix: fail open when tray recovery is lost"
```

### Task 5: Make settings reusable and route engine failure to shutdown

**Files:**
- Modify: `settings_window.py:297-511`
- Modify: `main.py:215-282`
- Modify: `main.py:417-471`
- Test: `tests/test_settings_window.py`
- Test: `tests/test_main.py`

- [ ] **Step 1: Add failing reusable-window tests**

Add `test_settings_window_installs_permanent_close_protocol` and `test_settings_window_is_reusable_after_cancel_and_titlebar_close`. Using the existing Tk fakes, assert:

- the `WM_DELETE_WINDOW` protocol is installed during construction, not only during recording;
- Cancel and title-bar close call recording cleanup and `withdraw()`;
- neither close path destroys the `Toplevel`;
- `show()`, close, and `show()` again succeeds and refreshes displayed values.

- [ ] **Step 2: Add failing settings engine-failure tests**

Add `test_engine_unhealthy_routes_every_settings_handler_to_fatal_shutdown`, parameterized over begin recording, save/replacement, key capture, focus loss, close/exit recording, and `on_lock_state()` while recording. Raise `EngineUnhealthy` and assert the application-level fatal callback is invoked exactly once. Assert that it enters fail-open and runs coordinated shutdown instead of merely displaying a settings dialog.

- [ ] **Step 3: Run focused settings tests and verify RED**

```powershell
py -m pytest tests/test_settings_window.py tests/test_main.py -k "reusable or permanent_close or withdraw or engine_unhealthy_routes" -v
```

Expected: reusable-window assertions fail because `_close()` destroys the `Toplevel`; unhealthy-routing assertions fail because handlers currently only display errors.

- [ ] **Step 4: Keep one reusable settings window**

Install the close protocol in `SettingsWindow.__init__`. Change `_close()` to end/cancel recording, unbind recording-only key handlers, reset the view from accepted settings as needed, and withdraw the window. `_unbind_recording_events()` must not clear the permanent close protocol.

- [ ] **Step 5: Add one application-level unhealthy callback**

Inject a callback into `SettingsWindow` for `EngineUnhealthy`. Route every settings handler that can receive this exception through the callback. In `create_application()`, connect it to the same `AppLifecycle` fatal helper used by hook/tray failures; use a narrowly scoped construction closure or queue to avoid a new global.

Validation, warning-declined, persistence, and registry errors that do not poison the hook remain ordinary settings errors and do not terminate the application.

- [ ] **Step 6: Run settings and lifecycle tests and verify GREEN**

```powershell
py -m pytest tests/test_settings_window.py tests/test_main.py -v
```

Expected: all settings/lifecycle tests pass, including repeat open/close and every unhealthy route.

- [ ] **Step 7: Commit reusable settings and fatal routing**

```powershell
git add -- settings_window.py main.py tests/test_settings_window.py tests/test_main.py
git commit -m "fix: harden settings window lifecycle"
```

### Task 6: Synchronize Start-with-Windows state from both UI entry points

**Files:**
- Modify: `settings_window.py:110-123`
- Modify: `settings_window.py:283-289`
- Modify: `settings_window.py:427-434`
- Modify: `main.py:293-314`
- Test: `tests/test_settings_window.py`
- Test: `tests/test_main.py`

- [ ] **Step 1: Add a failing cross-presentation test**

Add `test_settings_startup_change_updates_lifecycle_tray_and_view`. Start with registry state disabled. Toggle it on from the settings checkbox, then assert:

```python
assert app._startup_enabled is True
assert tray.state.startup_enabled is True
assert settings_window.view.startup_enabled is True
```

Invoke the tray Startup action next and assert that it requests `False`, not `True` again.

- [ ] **Step 2: Add registry-failure synchronization coverage**

Add `test_settings_startup_failure_publishes_actual_state_everywhere`. When a settings-originated registry write fails but `StartupUpdateResult.enabled` contains the refreshed actual value, assert that value is sent to the lifecycle, tray, and settings view while TOML remains unchanged.

Add `test_settings_startup_failure_with_unknown_state_preserves_presentations`. When `StartupUpdateResult.enabled is None`, preserve the prior lifecycle, tray, and settings values and report the registry error.

- [ ] **Step 3: Run focused tests and verify RED**

```powershell
py -m pytest tests/test_settings_window.py tests/test_main.py -k "settings_startup_change or settings_startup_failure" -v
```

Expected: the cross-presentation tests fail because settings currently updates only its local view.

- [ ] **Step 4: Centralize presentation of `StartupUpdateResult`**

Factor one main-thread `AppLifecycle.apply_startup_result(result: StartupUpdateResult) -> None` method. When `result.enabled` is not `None`, update `_startup_enabled`, tray presentation, and settings presentation; when it is `None`, preserve all three previous values. Report `result.error` after presentation reconciliation.

Pass `SettingsWindow` a `Callable[[StartupUpdateResult], None]` callback through a narrowly scoped construction closure. Its checkbox handler performs the coordinator operation once, then sends the returned result to `apply_startup_result`. The tray entry point performs the same operation and calls the same result method. Do not persist startup state in TOML.

- [ ] **Step 5: Run settings/main tests and verify GREEN**

```powershell
py -m pytest tests/test_settings_window.py tests/test_main.py -v
```

Expected: all settings/main tests pass and both startup entry points share the same presentation state.

- [ ] **Step 6: Commit startup-state synchronization**

```powershell
git add -- settings_window.py main.py tests/test_settings_window.py tests/test_main.py
git commit -m "fix: synchronize startup state presentations"
```

## Chunk 3: Source startup, cleanup, metadata, and release verification

### Task 7: Correct source-mode startup composition

**Files:**
- Modify: `main.py:340-420`
- Modify: `settings.py:23-31` only if command construction needs a narrowly scoped helper
- Test: `tests/test_main.py`
- Test: `tests/test_settings.py`

- [ ] **Step 1: Add failing composition tests for frozen and source modes**

Capture the command passed to the startup-registry factory.

For a frozen process, assert:

```text
"C:\Program Files\CatLocker\CatLocker.exe" --startup
```

For source execution with sibling `pythonw.exe` available, assert full command equality, for example:

```text
"C:\Program Files\Python\pythonw.exe" "D:\Repositories\catlocker\main.py" --startup
```

When sibling `pythonw.exe` is absent, the exact fallback is resolved `sys.executable`; assert full equality such as:

```text
"C:\Portable Python\python.exe" "D:\Repositories\catlocker\main.py" --startup
```

Assert that merely ending in `.exe` does not make an interpreter a frozen CatLocker build.

- [ ] **Step 2: Add source portable-path coverage**

In source mode, configuration path discovery must use the CatLocker source/application directory rather than the Python installation directory. Add one test with a pre-existing source-adjacent `catlocker.toml` and another without it; the absent case must resolve to `%LOCALAPPDATA%\CatLocker\config.toml`.

- [ ] **Step 3: Run focused startup tests and verify RED**

```powershell
py -m pytest tests/test_main.py tests/test_settings.py -k "source_mode or frozen_mode or startup_command or portable" -v
```

- [ ] **Step 4: Implement explicit mode-sensitive composition**

Use `sys.frozen` as the only frozen-build discriminator:

- frozen: executable is the packaged CatLocker executable; omit a script argument;
- source: application root is the directory containing `main.py`; include its absolute path and prefer the sibling `pythonw.exe` for the Run-key command;
- if sibling `pythonw.exe` is unavailable, use resolved `sys.executable` as the exact fallback and cover the full resulting command with a test.

Keep `build_startup_command()` responsible only for quoting its already-resolved executable and optional script.

- [ ] **Step 5: Run startup/settings tests and verify GREEN**

```powershell
py -m pytest tests/test_main.py tests/test_settings.py -v
```

Expected: all startup/settings tests pass in frozen, source-pythonw, source-fallback, portable-present, and portable-absent cases.

- [ ] **Step 6: Commit source startup correction**

```powershell
git add -- main.py settings.py tests/test_main.py tests/test_settings.py
git commit -m "fix: build valid source startup commands"
```

### Task 8: Remove legacy implementation and correct user-facing metadata

**Files:**
- Modify: `main.py:13-25`
- Delete from `main.py`: `legacy_main()` and all unreachable legacy UI below it
- Modify: `tray.py:154-164`
- Modify: `PAD.xml`
- Modify: `README.md:89-93`
- Modify: `catlocker.iss:7-24`
- Modify: `tests/test_packaging.py`

- [ ] **Step 1: Add failing packaging/metadata assertions**

Assert that:

- `main.py` contains no `legacy_main`, `import core`, `lock_mouse`, or deleted asset references;
- legacy-only `refresh_rate` and `load_asset` symbols are absent from `main.py`;
- tray labels are exactly `Lock Keyboard`, `Unlock Keyboard`, and `Toggle Cat Mode`;
- every repository-backed `PAD.xml` URL points to the fork: project URL `https://github.com/robbie4116/catlocker`, versioned release download below that repository, screenshot `https://raw.githubusercontent.com/robbie4116/catlocker/main/thumbnail.png`, and icon `https://raw.githubusercontent.com/robbie4116/catlocker/main/assets/icon.png`;
- no `Axorax/keylock` repository or raw-resource URL remains in PAD, while the separate Axorax author name/site/email attribution remains unchanged;
- PAD describes a configurable toggle shortcut and fixed emergency recovery, not a configurable emergency shortcut;
- README names the retained license as AGPL-3.0;
- the installer contains exactly `MinVersion=10.0.22000` in `[Setup]`.

- [ ] **Step 2: Run packaging tests and verify RED**

```powershell
py -m pytest tests/test_packaging.py tests/test_tray.py -k "legacy or metadata or labels or installer" -v
```

- [ ] **Step 3: Remove unreachable legacy code**

Delete `legacy_main()` completely. Remove helpers/constants used only by it, including `refresh_rate` and `load_asset` if repository search confirms no live reference. Keep `resource_path()` and the real `create_application().run()` entry point.

- [ ] **Step 4: Correct labels and metadata**

Update the native tray labels and every PAD repository/resource URL listed in Step 1. Preserve Axorax attribution and the existing AGPL-3.0 license. Keep the application Windows 11-only for this release and add exactly `MinVersion=10.0.22000` under `[Setup]`; do not claim Windows 10 support without a separate compatibility decision and test pass.

- [ ] **Step 5: Run packaging and tray tests and verify GREEN**

```powershell
py -m pytest tests/test_packaging.py tests/test_tray.py -v
```

Expected: all packaging/tray tests pass with no legacy references, stale repository URLs, label mismatch, or missing Windows 11 minimum.

- [ ] **Step 6: Commit cleanup and metadata**

```powershell
git add -- main.py tray.py PAD.xml README.md catlocker.iss tests/test_packaging.py tests/test_tray.py
git commit -m "chore: remove legacy code and fix release metadata"
```

### Task 9: Perform complete automated and portable-build verification

**Files:**
- Modify if needed: `docs/windows-manual-test-checklist.md`
- Generated/ignored: `.hardening-build-env/`, `build/`, `dist/CatLocker.exe`, `CatLocker.spec`

- [ ] **Step 1: Create a fresh pinned verification environment**

From `D:\Repositories\catlocker`, run:

```powershell
$repoRoot = (Resolve-Path -LiteralPath ".").Path.TrimEnd("\")
$venvPath = [System.IO.Path]::GetFullPath((Join-Path $repoRoot ".hardening-build-env"))
$repoPrefix = $repoRoot + [System.IO.Path]::DirectorySeparatorChar
if (-not $venvPath.StartsWith($repoPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing to remove environment outside repository: $venvPath"
}
if (Test-Path -LiteralPath $venvPath) {
    Remove-Item -LiteralPath $venvPath -Recurse -Force
}
py -m venv .hardening-build-env
.\.hardening-build-env\Scripts\python.exe -m pip install --disable-pip-version-check -r requirements-dev.txt
.\.hardening-build-env\Scripts\python.exe --version
.\.hardening-build-env\Scripts\python.exe -m pytest --version
.\.hardening-build-env\Scripts\python.exe -m PyInstaller --version
```

Expected: the exact old environment is removed, a fresh isolated environment is created, and installation succeeds using the pinned top-level requirements `pytest==8.4.2` and `pyinstaller==6.15.0`; record Python and tool versions. Do not upgrade packages beyond `requirements-dev.txt`.

- [ ] **Step 2: Run the complete unit suite**

```powershell
.\.hardening-build-env\Scripts\python.exe -m pytest -v
```

Expected: all existing and newly added tests pass with no warnings. The total must be greater than 217.

- [ ] **Step 3: Compile every production, build, and test module**

```powershell
.\.hardening-build-env\Scripts\python.exe -m compileall -q hotkeys.py keyboard_hook.py settings.py controller.py tray.py settings_window.py main.py build.py tests
```

Expected: exit code 0 and no output.

- [ ] **Step 4: Run repository hygiene checks**

```powershell
git diff --check 630747b..HEAD
git diff --check
$legacyMatches = rg -n "pynput|import six|WH_MOUSE|GetAsyncKeyState|legacy_main|lock_mouse|import core" --glob "*.py" .
if ($LASTEXITCODE -eq 0) { $legacyMatches; throw "Legacy or prohibited runtime references remain." }
if ($LASTEXITCODE -ne 1) { throw "rg failed while checking legacy references." }
```

Expected: both diff checks succeed and the negative repository search succeeds with no matches.

- [ ] **Step 5: Remove only verified generated build outputs**

```powershell
$repoRoot = (Resolve-Path -LiteralPath ".").Path.TrimEnd("\")
$expectedRoot = "D:\Repositories\catlocker"
if (-not $repoRoot.Equals($expectedRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Unexpected repository root: $repoRoot"
}
$targets = @(
    (Join-Path $repoRoot "build"),
    (Join-Path $repoRoot "dist"),
    (Join-Path $repoRoot "CatLocker.spec")
)
$prefix = $repoRoot + [System.IO.Path]::DirectorySeparatorChar
foreach ($target in $targets) {
    $resolvedTarget = [System.IO.Path]::GetFullPath($target)
    if (-not $resolvedTarget.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to remove path outside repository: $resolvedTarget"
    }
    if (Test-Path -LiteralPath $resolvedTarget) {
        Remove-Item -LiteralPath $resolvedTarget -Recurse -Force
    }
}
```

Expected: only the three named generated targets are absent afterward; source and tracked files remain untouched.

- [ ] **Step 6: Produce a clean portable build in the pinned environment**

```powershell
.\.hardening-build-env\Scripts\python.exe build.py
if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed." }
if (-not (Test-Path -LiteralPath "dist\CatLocker.exe")) { throw "CatLocker.exe was not created." }
```

Expected: PyInstaller 6.15.0 exits 0 and creates `dist\CatLocker.exe` from a clean analysis/build directory.

- [ ] **Step 7: Assert that PyInstaller has no CatLocker-relevant warnings**

```powershell
$warningPath = "build\CatLocker\warn-CatLocker.txt"
$badWarnings = Select-String -LiteralPath $warningPath -Pattern "missing module named (core|format|pynput|six)"
if ($badWarnings) { $badWarnings; throw "CatLocker-relevant PyInstaller warnings remain." }
Get-Content -LiteralPath $warningPath
```

Expected: the negative assertion succeeds. Report the remaining warning file contents; standard conditional POSIX-module warnings on Windows are acceptable.

- [ ] **Step 8: Record the portable executable hash**

```powershell
$artifactHash = (Get-FileHash -Algorithm SHA256 -LiteralPath "dist\CatLocker.exe").Hash
$artifactHash
```

Record the new SHA-256 in the final handoff and update the manual checklist artifact field if it contains the superseded hash.

- [ ] **Step 9: Run a 90-second native smoke test**

Launch only the exact rebuilt artifact:

```powershell
$existingCatLocker = Get-Process -Name CatLocker -ErrorAction SilentlyContinue
if ($existingCatLocker) { throw "Close existing CatLocker processes before the smoke test." }
$smokeStarted = [DateTime]::UtcNow
$catLockerProcess = Start-Process -FilePath (Resolve-Path -LiteralPath "dist\CatLocker.exe") -PassThru
$smokeDeadline = [DateTime]::UtcNow.AddSeconds(90)
Start-Sleep -Seconds 2
$catLockerProcess.Refresh()
if ($catLockerProcess.HasExited) { throw "CatLocker exited during startup." }
```

Within the 90-second deadline, use Windows UI control to verify tray-only unlocked startup, choose `Settings`, close it with Cancel, open `Settings` again, close it again, and choose tray Exit. Then run:

```powershell
$remainingMs = [Math]::Max(0, [int]($smokeDeadline - [DateTime]::UtcNow).TotalMilliseconds)
$parentExited = $catLockerProcess.WaitForExit($remainingMs)
$remainingCatLocker = @(Get-Process -Name CatLocker -ErrorAction SilentlyContinue | Where-Object { $_.StartTime.ToUniversalTime() -ge $smokeStarted })
if (-not $parentExited -or $remainingCatLocker.Count -gt 0) {
    $remainingCatLocker | Stop-Process -Force -ErrorAction SilentlyContinue
    if (-not $catLockerProcess.HasExited) { Stop-Process -Id $catLockerProcess.Id -Force -ErrorAction SilentlyContinue }
    throw "CatLocker or a one-file child remained after the smoke-test deadline."
}
```

Pass criteria: both Settings openings work, tray Exit succeeds, and no CatLocker parent/child process created by the smoke run remains. If UI control is unavailable, run the following bounded cleanup before marking the step pending rather than passed:

```powershell
@(Get-Process -Name CatLocker -ErrorAction SilentlyContinue | Where-Object { $_.StartTime.ToUniversalTime() -ge $smokeStarted }) |
    Stop-Process -Force -ErrorAction SilentlyContinue
$cleanupDeadline = [DateTime]::UtcNow.AddSeconds(10)
do {
    $remainingCatLocker = @(Get-Process -Name CatLocker -ErrorAction SilentlyContinue | Where-Object { $_.StartTime.ToUniversalTime() -ge $smokeStarted })
    if ($remainingCatLocker.Count -eq 0) { break }
    Start-Sleep -Milliseconds 100
} while ([DateTime]::UtcNow -lt $cleanupDeadline)
if ($remainingCatLocker.Count -gt 0) { throw "Smoke-test cleanup left CatLocker processes running." }
```

Do not mark unperformed physical-key or installer scenarios complete.

- [ ] **Step 10: Update the manual checklist accurately**

Record only the new hash, environment versions, automated results, and smoke cases actually performed. Leave these explicitly pending unless actually completed:

- physical Stream Deck F24 testing;
- broader keyboard/media/volume and chaotic-input hardware matrix;
- visual tray/menu review beyond the smoke path;
- Explorer restart behavior;
- login startup observation;
- Inno Setup build while `ISCC.exe` is unavailable.

If the checklist changed, commit it before the final verification rerun:

```powershell
git add -- docs/windows-manual-test-checklist.md
git diff --cached --quiet
if ($LASTEXITCODE -ne 0) { git commit -m "docs: record CatLocker hardening verification" }
```

- [ ] **Step 11: Re-run final verification after every tracked change**

```powershell
.\.hardening-build-env\Scripts\python.exe -m pytest -v
.\.hardening-build-env\Scripts\python.exe -m compileall -q hotkeys.py keyboard_hook.py settings.py controller.py tray.py settings_window.py main.py build.py tests
git diff --check 630747b..HEAD
git diff --check
git status --short --branch
```

Expected: the complete suite passes with more than 217 tests, compilation and both diff checks succeed, and the working tree is clean. If any tracked file changes after this step, repeat Step 11.

- [ ] **Step 12: Push directly to the requested remote branch and assert equality**

```powershell
git push origin HEAD:codex/catlocker-native-windows
if ($LASTEXITCODE -ne 0) { throw "git push failed." }
git fetch origin codex/catlocker-native-windows
if ($LASTEXITCODE -ne 0) { throw "git fetch failed." }
$localCommit = git rev-parse HEAD
$remoteCommit = git rev-parse origin/codex/catlocker-native-windows
if ($localCommit -ne $remoteCommit) { throw "Local and remote branch heads differ." }
$localCommit
$remoteCommit
```

Expected: push and fetch succeed and both printed hashes are identical.

- [ ] **Step 13: Provide the execution handoff**

Report:

- final commit hash and pushed branch;
- exact test count and command result;
- compile and diff-check results;
- portable build result, SHA-256, and warning inspection;
- native smoke-test observations;
- every remaining manual/hardware/installer item without overstating completion;
- any deviations from this plan;
- final working-tree status.
