"""Session-scoped instance ownership and activation signaling for CatLocker.

This module owns exactly two OS-backed resources: a named mutex that grants
"first instance" ownership of a Windows user session, and a named auto-reset
event that a duplicate launch uses to ask the owner to show its existing UI.
It knows nothing about Tk, application composition, or CLI argument parsing
(see `main.py` for that) -- it is exclusively about instance resources and
the activation signal.

Identity is derived from the current user's SID (never from
`sys.executable`/`__file__`/argv, and never from the environment username),
so an installed copy, a portable copy, and `python main.py` from source all
resolve to the same mutex/event names within one Windows logon session.

Win32 imports are lazy: nothing in this module touches `ctypes.WinDLL` at
import time, or even at `Win32InstanceAdapter()` construction time -- only
when one of its methods actually makes a Win32 call.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Protocol


# --- Win32 constants -------------------------------------------------------
# Kept here (rather than buried in the adapter) so tests can reference them
# without importing ctypes.
ERROR_ALREADY_EXISTS = 183
ERROR_FILE_NOT_FOUND = 2
EVENT_MODIFY_STATE = 0x0002
WAIT_OBJECT_0 = 0x00000000
WAIT_TIMEOUT = 0x00000102
WAIT_FAILED = 0xFFFFFFFF

DEFAULT_APP_NAME = "CatLocker"
DEFAULT_ACTIVATION_TIMEOUT = 2.0
_ACTIVATION_RETRY_INTERVAL = 0.05  # seconds between OpenEventW retries.


class InstanceError(RuntimeError):
    """Raised when instance ownership or activation hits an unexpected OS error.

    A caller (main.py, in Task 3) is expected to catch this, report an error,
    and exit without ever constructing the application.
    """


class InstanceRole(Enum):
    OWNER = "owner"
    DUPLICATE = "duplicate"


@dataclass(frozen=True)
class AcquireResult:
    """Typed outcome of `InstanceGuard.acquire()` -- never a bare handle/bool."""

    role: InstanceRole

    @property
    def is_owner(self) -> bool:
        return self.role is InstanceRole.OWNER

    @property
    def is_duplicate(self) -> bool:
        return self.role is InstanceRole.DUPLICATE


class WaitOutcome(Enum):
    SIGNALED = "signaled"
    TIMEOUT = "timeout"
    FAILED = "failed"


@dataclass(frozen=True)
class MutexResult:
    """Result of attempting to create/own the named instance mutex.

    `handle` is None on a hard OS error (including access denied) -- a
    non-null handle is returned by CreateMutexW both when this call created
    the mutex AND when it already existed, so `already_existed` (derived
    from GetLastError() == ERROR_ALREADY_EXISTS, captured immediately) is
    what actually distinguishes owner from duplicate.
    """

    handle: object | None
    already_existed: bool
    error_code: int | None = None


@dataclass(frozen=True)
class EventResult:
    """Result of attempting to create or open the named activation event."""

    handle: object | None
    error_code: int | None = None


class InstanceAdapter(Protocol):
    """Injectable seam for the OS primitives this module needs.

    A real implementation (`Win32InstanceAdapter`) lazily binds ctypes Win32
    calls; tests use a fake that simulates the OS's named-object namespace
    in memory.
    """

    def current_user_sid(self) -> str: ...

    def create_mutex(self, name: str) -> MutexResult: ...

    def create_event(self, name: str) -> EventResult: ...

    def open_event(self, name: str) -> EventResult: ...

    def set_event(self, handle: object) -> bool: ...

    def wait(self, handle: object, timeout_ms: int) -> WaitOutcome: ...

    def release_mutex(self, handle: object) -> None: ...

    def close_handle(self, handle: object) -> None: ...


def instance_names(sid: str, app_name: str = DEFAULT_APP_NAME) -> tuple[str, str]:
    """Build the (mutex, event) names for `sid`.

    Identical regardless of executable path, installed/portable mode, or how
    CatLocker was launched -- this function only ever sees `sid`, which the
    adapter derives from the current process token, never from
    `sys.executable`/`__file__`/argv or the environment username. `Local\\`
    scopes the names to the current Windows session; the SID further scopes
    them to the account within that session.
    """

    return (
        f"Local\\{app_name}.{sid}.Instance",
        f"Local\\{app_name}.{sid}.Activate",
    )


class InstanceGuard:
    """Owns the mutex/event pair that implement one-instance-per-session.

    Usage:
        guard = InstanceGuard()
        result = guard.acquire()          # raises InstanceError on hard OS failure
        if result.is_duplicate:
            guard.request_activation()    # ask the owner to show its UI
        else:
            ...run the app, polling guard.poll_activation()...
        guard.close()                     # idempotent
    """

    def __init__(
        self,
        *,
        adapter: InstanceAdapter | None = None,
        sid: str | None = None,
        app_name: str = DEFAULT_APP_NAME,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._adapter: InstanceAdapter = adapter if adapter is not None else Win32InstanceAdapter()
        self._sid = sid
        self._app_name = app_name
        self._clock = clock
        self._sleep = sleep

        self._role: InstanceRole | None = None
        self._mutex_name: str | None = None
        self._event_name: str | None = None
        self._mutex_handle: object | None = None
        self._event_handle: object | None = None
        self._closed = False

    @property
    def role(self) -> InstanceRole | None:
        return self._role

    def acquire(self) -> AcquireResult:
        if self._role is not None:
            raise RuntimeError("acquire() may only be called once per InstanceGuard.")

        sid = self._sid if self._sid is not None else self._adapter.current_user_sid()
        self._mutex_name, self._event_name = instance_names(sid, self._app_name)

        mutex = self._adapter.create_mutex(self._mutex_name)
        if mutex.handle is None:
            raise InstanceError(
                f"Failed to create instance mutex {self._mutex_name!r} "
                f"(error {mutex.error_code})."
            )

        if mutex.already_existed:
            # Duplicate: never keep this handle alive, not even through
            # request_activation() -- close it immediately.
            self._adapter.close_handle(mutex.handle)
            self._role = InstanceRole.DUPLICATE
            return AcquireResult(InstanceRole.DUPLICATE)

        # We are the first instance. Hold the mutex handle and create the
        # activation event before anything else (in particular, before any
        # application construction happens in main.py).
        self._mutex_handle = mutex.handle
        event = self._adapter.create_event(self._event_name)
        if event.handle is None:
            # Partial-initialization cleanup: release and close the mutex we
            # just created before the error propagates.
            self._adapter.release_mutex(self._mutex_handle)
            self._adapter.close_handle(self._mutex_handle)
            self._mutex_handle = None
            raise InstanceError(
                f"Failed to create activation event {self._event_name!r} "
                f"(error {event.error_code})."
            )

        self._event_handle = event.handle
        self._role = InstanceRole.OWNER
        return AcquireResult(InstanceRole.OWNER)

    def request_activation(self, timeout: float = DEFAULT_ACTIVATION_TIMEOUT) -> bool:
        """Ask the owner to show its UI. Only meaningful for a duplicate.

        Retries `OpenEventW` on a monotonic deadline while the event does not
        yet exist (the owner may still be starting up), and gives up
        immediately -- no retry -- on any other OS error (e.g. access
        denied). Returns True only after the event was opened AND
        successfully signaled.
        """

        if self._event_name is None:
            raise RuntimeError("request_activation() called before acquire().")
        if self._role is not InstanceRole.DUPLICATE:
            raise RuntimeError(
                "request_activation() is only valid after acquire() reports a duplicate instance."
            )

        deadline = self._clock() + timeout
        while True:
            opened = self._adapter.open_event(self._event_name)
            if opened.handle is not None:
                try:
                    return bool(self._adapter.set_event(opened.handle))
                finally:
                    self._adapter.close_handle(opened.handle)

            if opened.error_code != ERROR_FILE_NOT_FOUND:
                # Access denied or any other hard error: activation failure,
                # no point retrying.
                return False

            if self._clock() >= deadline:
                return False
            self._sleep(_ACTIVATION_RETRY_INTERVAL)

    def poll_activation(self) -> bool:
        """Zero-timeout poll of the owner's activation event.

        True if signaled (and auto-reset immediately consumes that signal),
        False on timeout. Only meaningful for the owner.
        """

        if self._role is not InstanceRole.OWNER:
            raise RuntimeError("poll_activation() is only valid for the owning instance.")

        outcome = self._adapter.wait(self._event_handle, 0)
        if outcome is WaitOutcome.SIGNALED:
            return True
        if outcome is WaitOutcome.TIMEOUT:
            return False
        raise InstanceError("WaitForSingleObject failed while polling the activation event.")

    def close(self) -> None:
        """Idempotent teardown. Safe even if acquire() was never called,
        raised, or only got partway through.

        Activation resources are closed before the mutex is released, and
        the mutex is released (and its handle closed) only for an owner --
        a duplicate's mutex handle was already closed inside acquire().
        """

        if self._closed:
            return
        self._closed = True

        if self._event_handle is not None:
            self._adapter.close_handle(self._event_handle)
            self._event_handle = None

        if self._mutex_handle is not None:
            self._adapter.release_mutex(self._mutex_handle)
            self._adapter.close_handle(self._mutex_handle)
            self._mutex_handle = None


# ---------------------------------------------------------------------------
# Real Win32-backed adapter
# ---------------------------------------------------------------------------

_dll_cache: dict[str, object] = {}


def _load_kernel32():
    """Lazily load and bind kernel32 functions. Never runs at import time.

    HANDLE-returning functions (CreateMutexW, CreateEventW, OpenEventW,
    GetCurrentProcess) MUST have their restype bound explicitly to
    `wintypes.HANDLE` (== c_void_p, pointer-sized). Left unset, ctypes
    defaults a function's return type to a 32-bit C `int`, which silently
    truncates/corrupts a real 64-bit kernel HANDLE value on 64-bit Windows.
    """

    dll = _dll_cache.get("kernel32")
    if dll is not None:
        return dll

    import ctypes
    from ctypes import wintypes

    dll = ctypes.WinDLL("kernel32", use_last_error=True)

    dll.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    dll.CreateMutexW.restype = wintypes.HANDLE

    dll.CreateEventW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
    dll.CreateEventW.restype = wintypes.HANDLE

    dll.OpenEventW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    dll.OpenEventW.restype = wintypes.HANDLE

    dll.SetEvent.argtypes = [wintypes.HANDLE]
    dll.SetEvent.restype = wintypes.BOOL

    dll.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    dll.WaitForSingleObject.restype = wintypes.DWORD

    dll.ReleaseMutex.argtypes = [wintypes.HANDLE]
    dll.ReleaseMutex.restype = wintypes.BOOL

    dll.CloseHandle.argtypes = [wintypes.HANDLE]
    dll.CloseHandle.restype = wintypes.BOOL

    dll.GetCurrentProcess.argtypes = []
    dll.GetCurrentProcess.restype = wintypes.HANDLE

    dll.LocalFree.argtypes = [wintypes.HLOCAL]
    dll.LocalFree.restype = wintypes.HLOCAL

    _dll_cache["kernel32"] = dll
    return dll


def _load_advapi32():
    """Lazily load and bind the advapi32 functions needed for SID lookup."""

    dll = _dll_cache.get("advapi32")
    if dll is not None:
        return dll

    import ctypes
    from ctypes import wintypes

    dll = ctypes.WinDLL("advapi32", use_last_error=True)

    dll.OpenProcessToken.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.HANDLE),
    ]
    dll.OpenProcessToken.restype = wintypes.BOOL

    dll.GetTokenInformation.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    dll.GetTokenInformation.restype = wintypes.BOOL

    dll.ConvertSidToStringSidW.argtypes = [wintypes.LPVOID, ctypes.POINTER(wintypes.LPWSTR)]
    dll.ConvertSidToStringSidW.restype = wintypes.BOOL

    _dll_cache["advapi32"] = dll
    return dll


class Win32InstanceAdapter:
    """Real Win32-backed `InstanceAdapter`.

    Constructing this class does nothing OS-related -- every method below
    lazily imports ctypes/wintypes and binds the DLL functions it needs on
    first use, so importing this module (or constructing this class) on a
    non-Windows machine, or in a test process that never calls a method
    here, never touches `ctypes.WinDLL`.
    """

    def create_mutex(self, name: str) -> MutexResult:
        import ctypes

        kernel32 = _load_kernel32()
        handle = kernel32.CreateMutexW(None, True, name)
        error = ctypes.get_last_error()
        if not handle:
            return MutexResult(handle=None, already_existed=False, error_code=error)
        return MutexResult(handle=handle, already_existed=(error == ERROR_ALREADY_EXISTS))

    def create_event(self, name: str) -> EventResult:
        import ctypes

        kernel32 = _load_kernel32()
        # bManualReset=False (auto-reset), bInitialState=False (unsignaled).
        handle = kernel32.CreateEventW(None, False, False, name)
        error = ctypes.get_last_error()
        if not handle:
            return EventResult(handle=None, error_code=error)
        return EventResult(handle=handle)

    def open_event(self, name: str) -> EventResult:
        import ctypes

        kernel32 = _load_kernel32()
        handle = kernel32.OpenEventW(EVENT_MODIFY_STATE, False, name)
        error = ctypes.get_last_error()
        if not handle:
            return EventResult(handle=None, error_code=error)
        return EventResult(handle=handle)

    def set_event(self, handle: object) -> bool:
        kernel32 = _load_kernel32()
        return bool(kernel32.SetEvent(handle))

    def wait(self, handle: object, timeout_ms: int) -> WaitOutcome:
        kernel32 = _load_kernel32()
        result = kernel32.WaitForSingleObject(handle, timeout_ms)
        if result == WAIT_OBJECT_0:
            return WaitOutcome.SIGNALED
        if result == WAIT_TIMEOUT:
            return WaitOutcome.TIMEOUT
        return WaitOutcome.FAILED

    def release_mutex(self, handle: object) -> None:
        kernel32 = _load_kernel32()
        kernel32.ReleaseMutex(handle)

    def close_handle(self, handle: object) -> None:
        kernel32 = _load_kernel32()
        kernel32.CloseHandle(handle)

    def current_user_sid(self) -> str:
        """Return the current process token's user SID as a string.

        Deliberately never derived from `os.environ["USERNAME"]` or any
        executable path -- only from the current token, via
        OpenProcessToken -> GetTokenInformation(TokenUser) ->
        ConvertSidToStringSidW.
        """

        import ctypes
        from ctypes import wintypes

        kernel32 = _load_kernel32()
        advapi32 = _load_advapi32()

        token_query = 0x0008
        token_user_class = 1  # TOKEN_INFORMATION_CLASS.TokenUser

        class _SidAndAttributes(ctypes.Structure):
            _fields_ = [("Sid", wintypes.LPVOID), ("Attributes", wintypes.DWORD)]

        class _TokenUser(ctypes.Structure):
            _fields_ = [("User", _SidAndAttributes)]

        process = kernel32.GetCurrentProcess()  # pseudo-handle; must not be closed
        token = wintypes.HANDLE()
        if not advapi32.OpenProcessToken(process, token_query, ctypes.byref(token)):
            raise InstanceError(
                f"OpenProcessToken failed (error {ctypes.get_last_error()})."
            )
        try:
            size = wintypes.DWORD(0)
            advapi32.GetTokenInformation(token, token_user_class, None, 0, ctypes.byref(size))
            if size.value == 0:
                raise InstanceError(
                    f"GetTokenInformation size probe failed (error {ctypes.get_last_error()})."
                )
            buffer = ctypes.create_string_buffer(size.value)
            ok = advapi32.GetTokenInformation(
                token, token_user_class, buffer, size, ctypes.byref(size)
            )
            if not ok:
                raise InstanceError(
                    f"GetTokenInformation failed (error {ctypes.get_last_error()})."
                )
            token_user = ctypes.cast(buffer, ctypes.POINTER(_TokenUser)).contents
            string_sid = wintypes.LPWSTR()
            if not advapi32.ConvertSidToStringSidW(
                token_user.User.Sid, ctypes.byref(string_sid)
            ):
                raise InstanceError(
                    f"ConvertSidToStringSidW failed (error {ctypes.get_last_error()})."
                )
            try:
                return string_sid.value
            finally:
                kernel32.LocalFree(string_sid)
        finally:
            kernel32.CloseHandle(token)
