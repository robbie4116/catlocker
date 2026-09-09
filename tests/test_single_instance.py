"""Tests for single_instance.py using a fake OS adapter.

These tests never touch real ctypes/Win32 APIs (except one guarded smoke test
that only constructs the real adapter without calling into Win32). A shared
FakeRegistry simulates the OS-level named-object namespace so multiple
FakeInstanceAdapter instances can represent multiple "processes" contending
for the same mutex/event names, the way real Windows processes would.
"""

from __future__ import annotations

import queue
import subprocess
import sys
import threading
import uuid
from pathlib import Path

import pytest

from single_instance import (
    DEFAULT_APP_NAME,
    ERROR_FILE_NOT_FOUND,
    EventResult,
    InstanceError,
    InstanceGuard,
    InstanceRole,
    MutexResult,
    Win32InstanceAdapter,
    WaitOutcome,
    instance_names,
)


ACCESS_DENIED = 5
DEFAULT_SID = "S-1-5-21-1111111111-2222222222-3333333333-1001"


class FakeRegistry:
    """Simulates the OS namespace shared across fake adapter "processes"."""

    def __init__(self) -> None:
        self.mutexes: dict[str, bool] = {}
        self.events: dict[str, bool] = {}


class FakeInstanceAdapter:
    def __init__(
        self,
        registry: FakeRegistry | None = None,
        *,
        sid: str = DEFAULT_SID,
        create_mutex_error: int | None = None,
        create_event_error: int | None = None,
        open_event_outcomes: list[str] | None = None,
        set_event_result: bool = True,
        wait_outcome: WaitOutcome | None = None,
    ) -> None:
        self.registry = registry if registry is not None else FakeRegistry()
        self.sid = sid
        self.calls: list[tuple] = []
        self.create_mutex_error = create_mutex_error
        self.create_event_error = create_event_error
        self._open_event_outcomes = (
            list(open_event_outcomes) if open_event_outcomes is not None else None
        )
        self.set_event_result = set_event_result
        self._wait_outcome_override = wait_outcome
        self._handle_names: dict[object, str] = {}
        self._open_handles: set[object] = set()
        self._next_handle = 1

    def _new_handle(self, name: str) -> object:
        handle = self._next_handle
        self._next_handle += 1
        self._handle_names[handle] = name
        self._open_handles.add(handle)
        return handle

    def current_user_sid(self) -> str:
        self.calls.append(("current_user_sid",))
        return self.sid

    def create_mutex(self, name: str) -> MutexResult:
        self.calls.append(("create_mutex", name))
        if self.create_mutex_error is not None:
            return MutexResult(handle=None, already_existed=False, error_code=self.create_mutex_error)
        already_existed = self.registry.mutexes.get(name, False)
        self.registry.mutexes[name] = True
        handle = self._new_handle(name)
        return MutexResult(handle=handle, already_existed=already_existed)

    def create_event(self, name: str) -> EventResult:
        self.calls.append(("create_event", name))
        if self.create_event_error is not None:
            return EventResult(handle=None, error_code=self.create_event_error)
        self.registry.events[name] = False
        handle = self._new_handle(name)
        return EventResult(handle=handle)

    def open_event(self, name: str) -> EventResult:
        self.calls.append(("open_event", name))
        if self._open_event_outcomes:
            outcome = self._open_event_outcomes.pop(0)
            if outcome == "not_found":
                return EventResult(handle=None, error_code=ERROR_FILE_NOT_FOUND)
            if outcome == "access_denied":
                return EventResult(handle=None, error_code=ACCESS_DENIED)
            # "ok" falls through to the normal registry lookup below.
        if name not in self.registry.events:
            return EventResult(handle=None, error_code=ERROR_FILE_NOT_FOUND)
        handle = self._new_handle(name)
        return EventResult(handle=handle)

    def set_event(self, handle: object) -> bool:
        self.calls.append(("set_event", handle))
        if not self.set_event_result:
            return False
        name = self._handle_names[handle]
        self.registry.events[name] = True
        return True

    def wait(self, handle: object, timeout_ms: int) -> WaitOutcome:
        self.calls.append(("wait", handle, timeout_ms))
        if self._wait_outcome_override is not None:
            return self._wait_outcome_override
        name = self._handle_names[handle]
        if self.registry.events.get(name, False):
            self.registry.events[name] = False
            return WaitOutcome.SIGNALED
        return WaitOutcome.TIMEOUT

    def release_mutex(self, handle: object) -> None:
        self.calls.append(("release_mutex", handle))

    def close_handle(self, handle: object) -> None:
        self.calls.append(("close_handle", handle))
        assert handle in self._open_handles, f"double-close of handle {handle!r}"
        self._open_handles.remove(handle)


def make_guard(adapter: FakeInstanceAdapter, **kwargs) -> InstanceGuard:
    return InstanceGuard(adapter=adapter, **kwargs)


class FakeClock:
    """A monotonic clock that advances by `step` seconds on every call."""

    def __init__(self, start: float = 0.0, step: float = 0.0) -> None:
        self.now = start
        self.step = step
        self.calls = 0

    def __call__(self) -> float:
        value = self.now
        self.now += self.step
        self.calls += 1
        return value


# ---------------------------------------------------------------------------
# Task 1 scenarios
# ---------------------------------------------------------------------------


def test_first_acquire_is_owner_second_is_duplicate():
    registry = FakeRegistry()
    owner_adapter = FakeInstanceAdapter(registry)
    duplicate_adapter = FakeInstanceAdapter(registry)

    owner_guard = make_guard(owner_adapter)
    duplicate_guard = make_guard(duplicate_adapter)

    owner_result = owner_guard.acquire()
    duplicate_result = duplicate_guard.acquire()

    assert owner_result.role is InstanceRole.OWNER
    assert owner_result.is_owner is True
    assert duplicate_result.role is InstanceRole.DUPLICATE
    assert duplicate_result.is_duplicate is True


def test_duplicate_closes_its_mutex_handle_immediately_before_returning():
    registry = FakeRegistry()
    make_guard(FakeInstanceAdapter(registry)).acquire()

    duplicate_adapter = FakeInstanceAdapter(registry)
    duplicate_guard = make_guard(duplicate_adapter)
    duplicate_guard.acquire()

    close_calls = [call for call in duplicate_adapter.calls if call[0] == "close_handle"]
    assert len(close_calls) == 1
    # The duplicate must never touch event-related adapter methods.
    assert not any(call[0] in ("create_event", "open_event") for call in duplicate_adapter.calls)


def test_mutex_hard_error_raises_and_creates_no_event():
    adapter = FakeInstanceAdapter(create_mutex_error=ACCESS_DENIED)
    guard = make_guard(adapter)

    with pytest.raises(InstanceError):
        guard.acquire()

    assert ("create_event", instance_names(adapter.sid)[1]) not in adapter.calls
    assert not any(call[0] == "create_event" for call in adapter.calls)
    # Only sid lookup + the failed mutex creation should have happened.
    assert [call[0] for call in adapter.calls] == ["current_user_sid", "create_mutex"]


def test_mutex_access_denied_never_yields_a_result_object():
    """A hard mutex failure must raise before any AcquireResult can be mistaken for success."""
    adapter = FakeInstanceAdapter(create_mutex_error=ACCESS_DENIED)
    guard = make_guard(adapter)

    try:
        result = guard.acquire()
    except InstanceError:
        result = None

    assert result is None


def test_partial_initialization_cleanup_releases_mutex_on_event_failure():
    adapter = FakeInstanceAdapter(create_event_error=ACCESS_DENIED)
    guard = make_guard(adapter)

    with pytest.raises(InstanceError):
        guard.acquire()

    call_names = [call[0] for call in adapter.calls]
    assert "release_mutex" in call_names
    assert call_names.count("close_handle") == 1
    # The mutex must be released before its handle is closed.
    assert call_names.index("release_mutex") < call_names.index("close_handle")
    # And both cleanup calls happen after the failed event creation.
    assert call_names.index("create_event") < call_names.index("release_mutex")


def test_close_is_idempotent_for_owner():
    adapter = FakeInstanceAdapter()
    guard = make_guard(adapter)
    guard.acquire()

    guard.close()
    guard.close()

    close_calls = [call for call in adapter.calls if call[0] == "close_handle"]
    release_calls = [call for call in adapter.calls if call[0] == "release_mutex"]
    assert len(close_calls) == 2  # one event handle, one mutex handle
    assert len(release_calls) == 1


def test_close_is_a_safe_no_op_for_duplicate():
    registry = FakeRegistry()
    make_guard(FakeInstanceAdapter(registry)).acquire()

    duplicate_adapter = FakeInstanceAdapter(registry)
    duplicate_guard = make_guard(duplicate_adapter)
    duplicate_guard.acquire()

    calls_before = list(duplicate_adapter.calls)
    duplicate_guard.close()
    duplicate_guard.close()

    assert duplicate_adapter.calls == calls_before  # close() added nothing further


def test_close_before_acquire_is_a_safe_no_op():
    adapter = FakeInstanceAdapter()
    guard = make_guard(adapter)

    guard.close()
    guard.close()

    assert adapter.calls == []


def test_instance_names_independent_of_executable_path(monkeypatch):
    monkeypatch.setattr(sys, "executable", r"C:\Program Files\CatLocker\CatLocker.exe")
    installed_names = instance_names(DEFAULT_SID)

    monkeypatch.setattr(sys, "executable", r"D:\Portable\CatLocker\CatLocker.exe")
    portable_names = instance_names(DEFAULT_SID)

    monkeypatch.setattr(sys, "argv", ["python.exe", "main.py"])
    script_names = instance_names(DEFAULT_SID)

    assert installed_names == portable_names == script_names
    assert installed_names == (
        f"Local\\{DEFAULT_APP_NAME}.{DEFAULT_SID}.Instance",
        f"Local\\{DEFAULT_APP_NAME}.{DEFAULT_SID}.Activate",
    )


def test_identical_names_across_guards_regardless_of_executable_context(monkeypatch):
    registry = FakeRegistry()

    monkeypatch.setattr(sys, "executable", r"C:\Program Files\CatLocker\CatLocker.exe")
    installed_adapter = FakeInstanceAdapter(registry, sid=DEFAULT_SID)
    make_guard(installed_adapter).acquire()

    monkeypatch.setattr(sys, "executable", r"D:\Portable\CatLocker.exe")
    portable_adapter = FakeInstanceAdapter(registry, sid=DEFAULT_SID)
    portable_result = make_guard(portable_adapter).acquire()

    installed_mutex_name = installed_adapter.calls[1][1]
    portable_mutex_name = portable_adapter.calls[1][1]
    assert installed_mutex_name == portable_mutex_name
    assert portable_result.is_duplicate  # second guard finds the first still owns it


# ---------------------------------------------------------------------------
# Task 2 scenarios
# ---------------------------------------------------------------------------


def test_request_activation_retries_until_event_appears_then_signals():
    registry = FakeRegistry()
    make_guard(FakeInstanceAdapter(registry)).acquire()

    duplicate_adapter = FakeInstanceAdapter(
        registry, open_event_outcomes=["not_found", "not_found", "ok"]
    )
    duplicate_guard = make_guard(duplicate_adapter)
    duplicate_guard.acquire()

    clock = FakeClock(start=0.0, step=0.0)
    sleeps: list[float] = []
    duplicate_guard._clock = clock
    duplicate_guard._sleep = sleeps.append

    result = duplicate_guard.request_activation(timeout=2.0)

    assert result is True
    open_calls = [call for call in duplicate_adapter.calls if call[0] == "open_event"]
    assert len(open_calls) == 3
    assert len(sleeps) >= 2  # slept between the two "not_found" attempts
    set_event_calls = [call for call in duplicate_adapter.calls if call[0] == "set_event"]
    assert len(set_event_calls) == 1
    # acquire() already closed the duplicate's mutex handle; request_activation
    # must close the (different) opened event handle afterward, in a finally path.
    close_calls = [call for call in duplicate_adapter.calls if call[0] == "close_handle"]
    assert len(close_calls) == 2
    mutex_close, event_close = close_calls
    assert event_close[1] != mutex_close[1]


def test_request_activation_gives_up_after_deadline_without_real_sleep():
    registry = FakeRegistry()
    make_guard(FakeInstanceAdapter(registry)).acquire()

    duplicate_adapter = FakeInstanceAdapter(
        registry, open_event_outcomes=["not_found"] * 20
    )
    duplicate_guard = make_guard(duplicate_adapter)
    duplicate_guard.acquire()

    clock = FakeClock(start=0.0, step=0.6)
    sleeps: list[float] = []
    duplicate_guard._clock = clock
    duplicate_guard._sleep = sleeps.append

    result = duplicate_guard.request_activation(timeout=2.0)

    assert result is False
    assert clock.calls >= 2
    # Confirm the retry loop actually iterated more than once before giving up.
    open_calls = [call for call in duplicate_adapter.calls if call[0] == "open_event"]
    assert len(open_calls) >= 2
    assert len(sleeps) >= 1


def test_request_activation_fails_promptly_on_hard_error_no_retry():
    registry = FakeRegistry()
    make_guard(FakeInstanceAdapter(registry)).acquire()

    duplicate_adapter = FakeInstanceAdapter(
        registry, open_event_outcomes=["access_denied"]
    )
    duplicate_guard = make_guard(duplicate_adapter)
    duplicate_guard.acquire()

    sleeps: list[float] = []
    duplicate_guard._sleep = sleeps.append

    result = duplicate_guard.request_activation(timeout=2.0)

    assert result is False
    open_calls = [call for call in duplicate_adapter.calls if call[0] == "open_event"]
    assert len(open_calls) == 1
    assert sleeps == []


def test_request_activation_fails_when_set_event_fails_and_still_closes_handle():
    registry = FakeRegistry()
    make_guard(FakeInstanceAdapter(registry)).acquire()

    duplicate_adapter = FakeInstanceAdapter(
        registry, open_event_outcomes=["ok"], set_event_result=False
    )
    duplicate_guard = make_guard(duplicate_adapter)
    duplicate_guard.acquire()

    result = duplicate_guard.request_activation(timeout=2.0)

    assert result is False
    # acquire() closed the duplicate's mutex handle; request_activation must
    # still close the opened (but unsuccessfully signaled) event handle.
    close_calls = [call for call in duplicate_adapter.calls if call[0] == "close_handle"]
    assert len(close_calls) == 2
    mutex_close, event_close = close_calls
    assert event_close[1] != mutex_close[1]


def test_poll_activation_true_when_signaled_false_on_timeout():
    adapter = FakeInstanceAdapter()
    guard = make_guard(adapter)
    guard.acquire()

    assert guard.poll_activation() is False  # nothing signaled yet

    name = instance_names(adapter.sid)[1]
    adapter.registry.events[name] = True

    assert guard.poll_activation() is True
    assert guard.poll_activation() is False  # auto-reset consumed the signal


def test_poll_activation_raises_on_wait_failure():
    adapter = FakeInstanceAdapter(wait_outcome=WaitOutcome.FAILED)
    guard = make_guard(adapter)
    guard.acquire()

    with pytest.raises(InstanceError):
        guard.poll_activation()


def test_coalesced_activation_requests_report_as_a_single_pending_signal():
    registry = FakeRegistry()
    owner_adapter = FakeInstanceAdapter(registry)
    owner_guard = make_guard(owner_adapter)
    owner_guard.acquire()

    for _ in range(2):
        duplicate_adapter = FakeInstanceAdapter(registry, open_event_outcomes=["ok"])
        duplicate_guard = make_guard(duplicate_adapter)
        duplicate_guard.acquire()
        assert duplicate_guard.request_activation(timeout=2.0) is True

    # Two successful SetEvent calls before any wait still yield exactly one
    # signaled poll, matching real auto-reset event semantics.
    assert owner_guard.poll_activation() is True
    assert owner_guard.poll_activation() is False


def test_close_order_closes_activation_resources_before_releasing_mutex():
    adapter = FakeInstanceAdapter()
    guard = make_guard(adapter)
    guard.acquire()

    guard.close()

    call_names = [call[0] for call in adapter.calls]
    event_close_index = call_names.index("close_handle")  # first close_handle is the event
    mutex_release_index = call_names.index("release_mutex")
    mutex_close_index = len(call_names) - 1 - call_names[::-1].index("close_handle")

    assert event_close_index < mutex_release_index
    assert event_close_index < mutex_close_index


# ---------------------------------------------------------------------------
# Misuse guards
# ---------------------------------------------------------------------------


def test_request_activation_before_acquire_raises():
    guard = make_guard(FakeInstanceAdapter())
    with pytest.raises(RuntimeError):
        guard.request_activation()


def test_request_activation_on_owner_raises():
    guard = make_guard(FakeInstanceAdapter())
    guard.acquire()
    with pytest.raises(RuntimeError):
        guard.request_activation()


def test_poll_activation_on_duplicate_raises():
    registry = FakeRegistry()
    make_guard(FakeInstanceAdapter(registry)).acquire()

    duplicate_guard = make_guard(FakeInstanceAdapter(registry))
    duplicate_guard.acquire()

    with pytest.raises(RuntimeError):
        duplicate_guard.poll_activation()


def test_acquire_twice_on_same_guard_raises():
    guard = make_guard(FakeInstanceAdapter())
    guard.acquire()
    with pytest.raises(RuntimeError):
        guard.acquire()


# ---------------------------------------------------------------------------
# Real adapter smoke test (construction only, never touches ctypes/Win32)
# ---------------------------------------------------------------------------


def test_win32_adapter_can_be_constructed_without_touching_win32():
    adapter = Win32InstanceAdapter()
    assert adapter is not None


# ---------------------------------------------------------------------------
# Native subprocess tests (Windows-only, real Win32 mutex/event objects)
# ---------------------------------------------------------------------------
#
# Everything above uses FakeInstanceAdapter and never touches ctypes/Win32.
# The tests below launch real `python -c ...` subprocesses that each
# construct a real `InstanceGuard` (default adapter -> a real
# `Win32InstanceAdapter()`, never a fake) against a unique, per-test
# `app_name` -- a fresh UUID suffix, e.g. "CatLockerNativeTest-<uuid4 hex>".
# That produces mutex/event names (`Local\CatLockerNativeTest-<hex>.<SID>.*`)
# completely disjoint from the real `Local\CatLocker.<SID>.*` names a
# genuinely running CatLocker instance on this machine would use, and from
# every other test's own random name. No test here can collide with,
# observe, or affect a real running CatLocker, or any other test's objects.
#
# Protocol: each child process is
#   python -c <_NATIVE_HELPER_SRC> <app_name> <mode>
# It always prints exactly one line -- "OWNER" or "DUPLICATE" -- immediately
# after `acquire()` returns, flushes, and then behaves per `mode`:
#   - "report_only": closes the guard and exits 0 right away.
#   - "acquire_then_block": a duplicate exits 0 immediately; an owner sleeps
#     (up to 30s) so the parent test can force-kill it while it still holds
#     the mutex. This is what makes "simultaneous acquisition" and "forced
#     termination" deterministic rather than a timing gamble: the owner
#     provably still holds the lock at the moment the test acts.
#   - "owner_poll_activation": must acquire as owner (exits 1 otherwise);
#     polls `poll_activation()` on a bounded loop and prints "ACTIVATED" (or
#     "TIMEOUT" then exits 1) as soon as a signal is observed.
#   - "duplicate_request_activation": must acquire as duplicate (exits 1
#     otherwise); calls `request_activation(timeout=2.0)` and prints
#     "ACTIVATED" or "FAILED".
#
# The parent test process never waits unboundedly: every subprocess read
# uses either `communicate(timeout=...)` or the queue-backed `_LineReader`
# below, and every test's `finally` block force-kills and reaps only the
# subprocess(es) it itself spawned (`_cleanup_processes`) -- no other
# process on the machine, and never the real `CatLocker` mutex/event
# namespace, is touched.

_NATIVE_TEST_ROOT = Path(__file__).resolve().parents[1]

_NATIVE_HELPER_SRC = r"""
import sys
import time

from single_instance import InstanceGuard

app_name = sys.argv[1]
mode = sys.argv[2]

guard = InstanceGuard(app_name=app_name)
result = guard.acquire()
print("OWNER" if result.is_owner else "DUPLICATE", flush=True)

if mode == "report_only":
    guard.close()
    sys.exit(0)

if mode == "acquire_then_block":
    if not result.is_owner:
        sys.exit(0)
    time.sleep(30)  # the parent test force-kills the owner well before this elapses
    sys.exit(1)  # pragma: no cover - should never be reached

if mode == "owner_poll_activation":
    if not result.is_owner:
        sys.exit(1)
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        if guard.poll_activation():
            print("ACTIVATED", flush=True)
            guard.close()
            sys.exit(0)
        time.sleep(0.02)
    print("TIMEOUT", flush=True)
    guard.close()
    sys.exit(1)

if mode == "duplicate_request_activation":
    if result.is_owner:
        sys.exit(1)
    ok = guard.request_activation(timeout=2.0)
    print("ACTIVATED" if ok else "FAILED", flush=True)
    guard.close()
    sys.exit(0)

sys.exit(2)  # unknown mode
"""


def _spawn_native_child(app_name: str, mode: str) -> subprocess.Popen:
    """Launch a real Python subprocess running `_NATIVE_HELPER_SRC`.

    `cwd=_NATIVE_TEST_ROOT` (the repo root) so `import single_instance`
    resolves in the child without mutating `sys.path` from inside the
    injected script.
    """

    return subprocess.Popen(
        [sys.executable, "-c", _NATIVE_HELPER_SRC, app_name, mode],
        cwd=_NATIVE_TEST_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _cleanup_processes(procs: list[subprocess.Popen]) -> None:
    """Force-terminate and reap only the given (test-owned) subprocesses.

    Safe to call on processes that already exited on their own. Never
    touches any process this test did not itself spawn.
    """

    for proc in procs:
        if proc.poll() is None:
            try:
                proc.kill()
            except OSError:
                pass
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        if proc.stdout is not None:
            proc.stdout.close()
        if proc.stderr is not None:
            proc.stderr.close()


class _LineReader:
    """Reads a subprocess's stdout on a background thread into a queue so a
    test can wait for a specific line with a bounded timeout even while the
    child process is still running (e.g. blocked in a poll loop).
    """

    def __init__(self, stream) -> None:
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._thread = threading.Thread(target=self._run, args=(stream,), daemon=True)
        self._thread.start()

    def _run(self, stream) -> None:
        try:
            for line in iter(stream.readline, ""):
                self._queue.put(line.rstrip("\n"))
        finally:
            self._queue.put(None)

    def next_line(self, timeout: float) -> str | None:
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            return None


@pytest.fixture
def unique_app_name() -> str:
    """A fresh per-test app_name, guaranteeing mutex/event names disjoint
    from any real CatLocker instance and from every other test run."""

    return f"CatLockerNativeTest-{uuid.uuid4().hex}"


@pytest.mark.skipif(
    sys.platform != "win32",
    reason="Native InstanceGuard races require real Win32 mutex/event objects.",
)
class TestNativeInstanceRaces:
    """Subprocess-based tests against the real `Win32InstanceAdapter`.

    These are the only tests in this file that exercise real
    `CreateMutexW`/`CreateEventW`/`OpenEventW`/`WaitForSingleObject` calls
    under genuine multi-process contention, rather than `FakeInstanceAdapter`.
    """

    def test_simultaneous_acquisition_yields_exactly_one_owner(self, unique_app_name):
        # All three race for the same name; the owner holds the mutex
        # (blocked in "acquire_then_block") for up to 30s, so every straggler
        # among the three is guaranteed to still see it held when it attempts
        # its own acquire() -- this is genuine contention, not a timing gamble.
        procs = [_spawn_native_child(unique_app_name, "acquire_then_block") for _ in range(3)]
        try:
            roles = []
            for proc in procs:
                reader = _LineReader(proc.stdout)
                line = reader.next_line(timeout=10)
                assert line in ("OWNER", "DUPLICATE"), (
                    f"child produced no role line (got {line!r})"
                )
                roles.append(line)
        finally:
            _cleanup_processes(procs)

        assert roles.count("OWNER") == 1, roles
        assert roles.count("DUPLICATE") == len(procs) - 1, roles

    def test_activation_event_delivered_to_polling_owner(self, unique_app_name):
        owner = _spawn_native_child(unique_app_name, "owner_poll_activation")
        try:
            reader = _LineReader(owner.stdout)
            first = reader.next_line(timeout=10)
            assert first == "OWNER", f"owner did not acquire ownership (got {first!r})"

            duplicate = _spawn_native_child(unique_app_name, "duplicate_request_activation")
            try:
                dup_out, dup_err = duplicate.communicate(timeout=10)
            finally:
                _cleanup_processes([duplicate])
            assert duplicate.returncode == 0, f"duplicate child failed: {dup_err}"
            assert dup_out.strip().splitlines() == ["DUPLICATE", "ACTIVATED"]

            # Headline assertion: the owner's own poll loop -- real
            # WaitForSingleObject(handle, 0) calls -- eventually observes the
            # duplicate's SetEvent. The read timeout here is intentionally
            # longer than the child's own 10s internal poll deadline above,
            # so a slow (e.g. antivirus-loaded CI) machine gets the child's
            # own "TIMEOUT" line and a clear assertion failure instead of
            # this read racing the child's deadline.
            activated = reader.next_line(timeout=12)
            assert activated == "ACTIVATED", "owner never observed the activation signal"

            assert owner.wait(timeout=10) == 0
        finally:
            _cleanup_processes([owner])

    def test_owner_normal_exit_allows_fresh_acquisition_afterward(self, unique_app_name):
        first = _spawn_native_child(unique_app_name, "report_only")
        try:
            out, err = first.communicate(timeout=10)
        finally:
            _cleanup_processes([first])
        assert first.returncode == 0, f"child failed: {err}"
        assert out.strip() == "OWNER"

        second = _spawn_native_child(unique_app_name, "report_only")
        try:
            out2, err2 = second.communicate(timeout=10)
        finally:
            _cleanup_processes([second])
        assert second.returncode == 0, f"child failed: {err2}"
        # Headline assertion: a normal owner exit (guard.close(), releasing
        # and closing the mutex handle) leaves no stale lock behind -- the
        # very next acquisition on the same name is again the owner.
        assert out2.strip() == "OWNER"

    def test_owner_forced_termination_allows_fresh_acquisition_afterward(self, unique_app_name):
        owner = _spawn_native_child(unique_app_name, "acquire_then_block")
        try:
            reader = _LineReader(owner.stdout)
            first = reader.next_line(timeout=10)
            assert first == "OWNER", f"owner did not acquire ownership (got {first!r})"

            # Simulate a crash: force-kill before InstanceGuard.close() (and
            # therefore ReleaseMutex/CloseHandle) ever runs.
            owner.kill()
            assert owner.wait(timeout=10) is not None
        finally:
            _cleanup_processes([owner])

        second = _spawn_native_child(unique_app_name, "report_only")
        try:
            out, err = second.communicate(timeout=10)
        finally:
            _cleanup_processes([second])
        assert second.returncode == 0, f"child failed: {err}"
        # Headline assertion: the kernel reclaims a process-owned mutex on
        # termination even without a graceful InstanceGuard.close() call --
        # process exit or a crash must never leave a stale lock behind.
        assert out.strip() == "OWNER"
