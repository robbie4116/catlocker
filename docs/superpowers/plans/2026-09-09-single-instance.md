# Single Instance Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:executing-plans to implement this plan in the separate implementation session. Steps use checkbox syntax for tracking. Do not implement during the planning session.

**Goal:** Keep one CatLocker per Windows user session and route repeat launches to the existing app without changing lock state.

**Architecture:** A named Windows mutex guards the entry point before application construction. A named auto-reset event carries activation requests; the existing main-thread pump polls it without blocking and queues a typed activation action. No listener thread, network endpoint, or payload protocol is needed.

**Tech Stack:** Python 3.11+, ctypes Win32 APIs, existing Tk main-thread lifecycle, pytest.

**Approved spec:** `docs/superpowers/specs/2026-09-09-tester-feedback-design.md`, section 2.

**Order:** Prefer after `2026-09-09-shortcut-modes.md`; do not overwrite changes it makes to `main.py` or Settings. This feature does not depend on its configuration schema.

## Chunk 1: Ownership, activation, and verification

### Task 1: Establish lifecycle contracts

**Create:** `single_instance.py`, `tests/test_single_instance.py`.
**Read:** `main.py:AppLifecycle`, entry point, `tests/test_main.py`.

- [x] Read the approved spec and current repository instructions; run `git status --short` and `py -m pytest -v`. Preserve unrelated work and record baseline failures. (Baseline after the shortcut-modes plan: 564 passed, 0 failures.)
- [x] Define a small injectable adapter for named mutex/event creation, opening, zero-timeout waits, signaling, and handle closure. Keep Win32 imports lazy and bind pointer-sized ctypes HANDLE return types explicitly.
- [x] Write fake-adapter tests for one owner versus duplicate, API failure, partial initialization cleanup, double-close prevention, and identical names across executable paths. Use a stable `Local\CatLocker.<user-SID>.Instance` mutex and `.Activate` event: Local scopes to session; SID scopes to account within that session. Obtain SID from the current token, never environment username or executable path.
- [x] Run `py -m pytest tests/test_single_instance.py -v`; confirm tests fail for missing behavior.
- [x] Implement the instance resource owner with `acquire()`, `request_activation(timeout=2.0)`, `poll_activation()`, and idempotent `close()` seams. Use a typed owner/duplicate result, not truthy raw handles. Keep this module exclusively about instance resources and activation.

### Task 2: Implement Windows ownership and signaling

**Modify:** `single_instance.py`, `tests/test_single_instance.py`.

- [x] Acquire a named mutex with `CreateMutexW` and initial ownership requested. Capture last error immediately. A non-null new mutex handle denotes the first instance. `ERROR_ALREADY_EXISTS` denotes a duplicate: close its mutex handle immediately, never keep it alive through activation retries. A null handle is an error, including access denied; do not fall back to a different name or start an app.
- [x] Only the first instance creates the named auto-reset, initially unsignaled activation event, before constructing the app. On failure, release/close its mutex and exit with an error. Hold the guard until app shutdown finishes. Close activation resources before releasing the guard; release owned mutex on the acquiring main thread and close its handle.
- [x] Implement duplicate activation with `OpenEventW(EVENT_MODIFY_STATE)` and `SetEvent`. If the event does not yet exist, retry on a monotonic deadline for at most two seconds with short intervals and an injectable clock. Access denied and other OS errors yield activation failure. Close every opened event handle in a finally path. Duplicates never create the event or attempt ownership takeover.
- [x] Poll the owner's event with `WaitForSingleObject(handle, 0)`. Return true for signaled, false for timeout; raise for wait failure. Auto-reset coalesces repeated launches into one pending activation. Signaling is delivery to the event, not acknowledgement of displayed UI; make no stronger guarantee if the owner crashes immediately afterward.
- [x] Add fake tests for owner-startup delay, retry expiry, failed signaling, event errors, coalesced requests, and resource ordering. Ensure mutex access-denied never reaches app construction. Run `py -m pytest tests/test_single_instance.py -v`; expected pass.
- [x] Commit the module and tests with message `feat: add session-scoped instance ownership`. (Commit `67aef9c`; Tasks 1 and 2 were combined into this one commit, matching the plan's own commit placement.)

### Task 3: Guard the real entry point

**Modify:** `main.py`.
**Test:** `tests/test_main.py`.

- [x] Add entry-point tests with injected guard/application factories: ownership occurs before any root/config/hook/tray construction; duplicate paths never call application factory; successful owner wraps construction and run in finally cleanup.
- [x] Test normal duplicate activation success, two-second failure fallback, duplicate `--startup` with no activation or dialog, owner construction failure, run failure, and ownership error. Keep `import main` side-effect-free.
- [x] Run `py -m pytest tests/test_main.py -v`; confirm intended new failures.
- [x] Add `main(argv=None, ...)` parsing the existing `--startup` flag. Owner constructs and runs the app, then closes its guard in finally. Keep `create_application()` as an injectable composition function so existing tests need no real OS guard. Production `__main__` calls only the guarded entry point.
- [x] For ordinary duplicate activation failure, display a brief message directing users to the existing CatLocker tray icon and exit. For duplicate startup, close handles and return quietly. For unexpected ownership failure, show an error and return nonzero without constructing the app. A small lazy Win32 MessageBox adapter can display these messages without creating an application root.
- [x] Rerun `py -m pytest tests/test_main.py tests/test_single_instance.py -v`; expected pass. Commit with message `feat: guard CatLocker startup against duplicate launches`. (Commit `9d6b02d`.)

### Task 4: Activate the existing UI safely

**Modify:** `main.py:AppLifecycle`, `settings_window.py:SettingsWindow` if needed.
**Test:** `tests/test_main.py`, `tests/test_settings_window.py`.

- [x] Add lifecycle tests that a signaled event queues a typed activation action, and only the Tk main thread presents UI. Recheck current controller lock state when handling the action, not when signaling. While closing, ignore activation.
- [x] Add tests: unlocked activation calls existing Settings show/raise; unsaved edits and recorder candidates survive reopening; locked activation shows the exact status message even with optional notifications disabled; activation never invokes lock/unlock/toggle. Poll failure is reported once and disables further activation polling while retaining mutex ownership and keyboard recovery.
- [x] Run `py -m pytest tests/test_main.py tests/test_settings_window.py -v`; confirm intended failures.
- [x] Inject the owner activation source into `AppLifecycle`. Poll once per existing 25ms pump iteration with zero timeout and enqueue an internal `ActivateExisting` action into `actions`. Handle it alongside tray actions. No new worker is needed; remove polling before guard cleanup.
- [x] When unlocked, call the existing Settings show method with focus on a visible field. Ensure it raises the same Toplevel without reloading saved values over drafts. If recording, preserve it unless existing focus-loss behavior cancels it naturally; do not reset candidates or mode as part of activation. (`SettingsWindow.show()` needed no changes — it already reads only in-progress drafts.)
- [x] When locked, show **CatLocker is already running; the keyboard is locked.** using a main-thread informational dialog independent of notification preferences. Parent it to the root. Guard against opening multiple status dialogs during Tk's nested modal loop; coalesce repeated activation while one is visible. Never open Settings or alter lock state.
- [x] Rerun focused tests; expected pass. Commit with message `feat: reopen the existing CatLocker instance`. (Commit `38c5f1e`, plus follow-up fix `7e14e59` from code review: an unguarded exception in the activation handler could have frozen the pump loop.)

### Task 5: Verify native races, packaging, and recovery

**Modify:** `README.md`, `docs/windows-manual-test-checklist.md`.
**Test:** `tests/test_single_instance.py`, `tests/test_main.py`.

- [x] Add Windows-only subprocess tests against the guard module, not the real keyboard hook. Use unique test object names with a UUID, injected by tests only. Verify simultaneous acquisition yields one owner, event delivery, owner normal exit, forced termination, and fresh acquisition afterward. Bound all waits and clean up only subprocesses the test created. Skip native tests on non-Windows; keep fake tests portable.
- [x] Run `py -m pytest tests/test_single_instance.py tests/test_main.py -v`, then `py -m pytest -v`; expected passing, with any platform skips explicitly reported. (597 passed, 0 skipped, 0 failed — this session ran on Windows so the native subprocess tests executed for real, not skipped.)
- [x] Run `py build.py`; expected success. Confirm packaging includes the new imported module. Run only one test owner at a time when physically checking hooks. (Build succeeded; `single_instance` confirmed present in `build/CatLocker/PYZ-00.toc`.)
- [~] Manually test packaged repeated launch, simultaneous launch, locked launch, unsaved Settings drafts, duplicate `--startup`, installed/portable paths using different configs, owner crash and restart, and standard/elevated launches in both orders. Verify one tray icon and hook; duplicate configs untouched; startup quiet; locked status visible with notifications off. Access-denied must yield an error and no second owner, even if cross-elevation activation cannot succeed. **Partially done** against the real packaged `dist\CatLocker.exe`, using process lists/exit codes rather than on-screen confirmation (see `docs/windows-manual-test-checklist.md`'s "Single Instance Retest" section for exactly what was and wasn't checked and why): repeated launch, simultaneous launch, duplicate `--startup`, and forced-termination-then-restart were confirmed at the process level. Locked-launch, unsaved-draft, installed/portable, and elevation scenarios were deliberately left for a human, since exercising them either risks locking the real keyboard mid-session or requires visually confirming on-screen state.
- [~] Check session/account independence with a second Windows session if available; otherwise record that case as pending. Exit test instances after checks; do not terminate the user's unrelated processes. No second Windows session was available this session; recorded as pending. All test-launched CatLocker instances were terminated after each check; no unrelated user processes were touched.
- [x] Document single-instance behavior and fallback wording in README and the manual checklist. Run `git diff --check`, inspect changes, and commit with message `test: verify single-instance lifecycle and reopening`. Report native/manual cases still pending separately from passing automated tests. (Commit `66cf73a`, plus follow-up timeout-robustness fix applied directly by the controller before committing: the activation-delivery native test's internal deadline was widened from 5s to 10s to match the file's other bounds, per a code-review finding.)

### Windows API references verified during planning

- [CreateMutexW](https://learn.microsoft.com/en-us/windows/win32/api/synchapi/nf-synchapi-createmutexw): existing-object detection and ownership/handle lifecycle.
- [Kernel object namespaces](https://learn.microsoft.com/en-us/windows/win32/termserv/kernel-object-namespaces): Local session namespace.
- [CreateEventW](https://learn.microsoft.com/en-us/windows/win32/api/synchapi/nf-synchapi-createeventw): auto-reset signaling semantics.
- [OpenEventW](https://learn.microsoft.com/en-us/windows/win32/api/synchapi/nf-synchapi-openeventw): opening an existing event with requested access.
- [WaitForSingleObject](https://learn.microsoft.com/en-us/windows/win32/api/synchapi/nf-synchapi-waitforsingleobject): zero-timeout polling and return handling.

The design chooses these primitives; physical cross-elevation and packaged behavior remains an implementation verification requirement. No Node tools are needed; if introduced, follow repository process-hygiene instructions.
