"""Tests for single_instance.py using a fake OS adapter.

These tests never touch real ctypes/Win32 APIs (except one guarded smoke test
that only constructs the real adapter without calling into Win32). A shared
FakeRegistry simulates the OS-level named-object namespace so multiple
FakeInstanceAdapter instances can represent multiple "processes" contending
for the same mutex/event names, the way real Windows processes would.
"""

from __future__ import annotations

import sys

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
