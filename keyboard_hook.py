from __future__ import annotations

import ctypes
import math
import queue
import threading
import time
from ctypes import wintypes
from dataclasses import dataclass, field
from enum import Enum, auto

from hotkeys import InputState, KeyEvent, Shortcut


WH_KEYBOARD_LL = 13
HC_ACTION = 0
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105
WM_QUIT = 0x0012
WM_APP_COMMAND = 0x8001
PM_NOREMOVE = 0x0000
LLKHF_INJECTED = 0x10

ULONG_PTR = ctypes.c_size_t
LRESULT = ctypes.c_ssize_t


class CommandKind(Enum):
    SET_LOCKED = auto()
    TOGGLE = auto()
    REPLACE_SHORTCUT = auto()
    ENTER_RECORDING = auto()
    EXIT_RECORDING = auto()
    FAIL_OPEN = auto()
    STOP = auto()


class _CommandLifecycle:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._canceled = False
        self._started = False
        self._commit_started = False
        self._cancellable = True

    def cancel(self) -> bool:
        with self._lock:
            if not self._cancellable or self._commit_started:
                return False
            self._canceled = True
            return True

    def begin(self, *, cancellable: bool = True) -> bool:
        with self._lock:
            if self._canceled or self._started:
                return False
            self._started = True
            self._cancellable = cancellable
            return True

    def commit(self, operation):
        with self._lock:
            if self._canceled:
                return False, None
            self._commit_started = True
            return True, operation()


@dataclass(frozen=True, slots=True)
class HookCommand:
    command_id: int
    kind: CommandKind
    payload: object = None
    reply: queue.SimpleQueue | None = None
    lifecycle: _CommandLifecycle = field(
        default_factory=_CommandLifecycle,
        compare=False,
        repr=False,
    )


@dataclass(frozen=True, slots=True)
class CommandResult:
    command_id: int
    accepted: bool
    locked: bool
    shortcut_generation: int


@dataclass(frozen=True, slots=True)
class EngineEvent:
    kind: str
    locked: bool
    reason: str | None = None
    error: BaseException | None = None


class HookStopped(RuntimeError):
    pass


class HookTimeout(TimeoutError):
    pass


def _require_finite_timeout(timeout: float, operation: str) -> float:
    try:
        value = float(timeout)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(
            f"{operation} timeout must be finite and non-negative."
        ) from exc
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"{operation} timeout must be finite and non-negative.")
    return value


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


def event_from_message(message: int, data: KBDLLHOOKSTRUCT) -> KeyEvent | None:
    if message in (WM_KEYDOWN, WM_SYSKEYDOWN):
        is_keydown = True
    elif message in (WM_KEYUP, WM_SYSKEYUP):
        is_keydown = False
    else:
        return None
    return KeyEvent(
        int(data.vkCode),
        is_keydown,
        bool(data.flags & LLKHF_INJECTED),
    )


HHOOK = ctypes.c_void_p
HINSTANCE = ctypes.c_void_p
HOOKPROC = ctypes.WINFUNCTYPE(
    LRESULT,
    ctypes.c_int,
    wintypes.WPARAM,
    wintypes.LPARAM,
)


class Win32Api:
    def __init__(self) -> None:
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        self.user32.SetWindowsHookExW.argtypes = [
            ctypes.c_int,
            HOOKPROC,
            HINSTANCE,
            wintypes.DWORD,
        ]
        self.user32.SetWindowsHookExW.restype = HHOOK

        self.user32.CallNextHookEx.argtypes = [
            HHOOK,
            ctypes.c_int,
            wintypes.WPARAM,
            wintypes.LPARAM,
        ]
        self.user32.CallNextHookEx.restype = LRESULT

        self.user32.UnhookWindowsHookEx.argtypes = [HHOOK]
        self.user32.UnhookWindowsHookEx.restype = wintypes.BOOL

        self.user32.GetMessageW.argtypes = [
            ctypes.POINTER(wintypes.MSG),
            wintypes.HWND,
            wintypes.UINT,
            wintypes.UINT,
        ]
        self.user32.GetMessageW.restype = wintypes.BOOL

        self.user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
        self.user32.TranslateMessage.restype = wintypes.BOOL

        self.user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
        self.user32.DispatchMessageW.restype = LRESULT

        self.user32.PostThreadMessageW.argtypes = [
            wintypes.DWORD,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        ]
        self.user32.PostThreadMessageW.restype = wintypes.BOOL

        self.user32.PeekMessageW.argtypes = [
            ctypes.POINTER(wintypes.MSG),
            wintypes.HWND,
            wintypes.UINT,
            wintypes.UINT,
            wintypes.UINT,
        ]
        self.user32.PeekMessageW.restype = wintypes.BOOL

        self.kernel32.GetCurrentThreadId.argtypes = []
        self.kernel32.GetCurrentThreadId.restype = wintypes.DWORD

        self.kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        self.kernel32.GetModuleHandleW.restype = HINSTANCE

        self.user32.PostQuitMessage.argtypes = [ctypes.c_int]
        self.user32.PostQuitMessage.restype = None

    def install_hook(self, callback: HOOKPROC) -> HHOOK:
        module_handle = self.kernel32.GetModuleHandleW(None)
        hook = self.user32.SetWindowsHookExW(
            WH_KEYBOARD_LL,
            callback,
            module_handle,
            0,
        )
        if not hook:
            raise ctypes.WinError(ctypes.get_last_error())
        return hook

    def initialize_message_queue(self) -> int:
        message = wintypes.MSG()
        return int(
            self.user32.PeekMessageW(
                ctypes.byref(message),
                None,
                0,
                0,
                PM_NOREMOVE,
            )
        )

    def get_current_thread_id(self) -> int:
        return int(self.kernel32.GetCurrentThreadId())

    def call_next(
        self,
        hook: HHOOK,
        n_code: int,
        w_param: int,
        l_param: int,
    ) -> int:
        return int(self.user32.CallNextHookEx(hook, n_code, w_param, l_param))

    def unhook(self, hook: HHOOK) -> bool:
        return bool(self.user32.UnhookWindowsHookEx(hook))

    def get_message(self, message: wintypes.MSG) -> int:
        return int(self.user32.GetMessageW(ctypes.byref(message), None, 0, 0))

    def translate(self, message: wintypes.MSG) -> bool:
        return bool(self.user32.TranslateMessage(ctypes.byref(message)))

    def dispatch(self, message: wintypes.MSG) -> int:
        return int(self.user32.DispatchMessageW(ctypes.byref(message)))

    def post_thread_message(
        self,
        thread_id: int,
        message: int,
        w_param: int = 0,
        l_param: int = 0,
    ) -> bool:
        return bool(
            self.user32.PostThreadMessageW(
                thread_id,
                message,
                w_param,
                l_param,
            )
        )

    def post_quit(self, exit_code: int = 0) -> None:
        self.user32.PostQuitMessage(exit_code)


class KeyboardHook:
    def __init__(
        self,
        shortcut: Shortcut,
        *,
        api: Win32Api | None = None,
        locked: bool = False,
    ) -> None:
        self.api = api if api is not None else Win32Api()
        self.state = InputState(shortcut, locked=locked)
        self.thread: threading.Thread | None = None
        self.ready = threading.Event()
        self.fail_open = threading.Event()
        self.events: queue.SimpleQueue[EngineEvent] = queue.SimpleQueue()
        self.thread_id: int | None = None
        self.hook_handle: HHOOK | int | None = None
        self.callback: HOOKPROC | None = None
        self.installation_exception: BaseException | None = None
        self.cleanup_exception: BaseException | None = None
        self.shortcut_generation = 0

        self._hook_lock = threading.Lock()
        self._startup_condition = threading.Condition()
        self._startup_cancelled = threading.Event()
        self._pending_lock = threading.Lock()
        self._pending_commands: dict[int, HookCommand] = {}
        self._next_command_id = 1
        self._stop_requested = threading.Event()

    @property
    def locked(self) -> bool:
        if self.fail_open.is_set():
            return False
        return self.state.locked

    def is_alive(self) -> bool:
        return self.thread is not None and self.thread.is_alive()

    def start(self, timeout: float = 1.0) -> None:
        timeout = _require_finite_timeout(timeout, "start")
        if self.thread is not None:
            raise HookStopped("Keyboard hook has already been started.")
        self.thread = threading.Thread(
            target=self._run,
            name="CatLockerKeyboardHook",
            daemon=True,
        )
        self.thread.start()
        deadline = time.monotonic() + timeout
        with self._startup_condition:
            while not self.ready.is_set():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self._startup_cancelled.set()
                    self.fail_open.set()
                    self._startup_condition.notify_all()
                    raise HookTimeout("Timed out starting keyboard hook.")
                self._startup_condition.wait(remaining)
        if self.installation_exception is not None:
            raise self.installation_exception

    def stop(self, timeout: float = 1.0) -> None:
        timeout = _require_finite_timeout(timeout, "stop")
        thread = self.thread
        if thread is None:
            self.fail_open.set()
            self.state.set_locked(False)
            self._raise_cleanup_error_if_needed()
            return
        if thread is threading.current_thread():
            raise HookStopped("Keyboard hook cannot stop itself.")

        deadline = time.monotonic() + timeout
        stop_error: HookStopped | HookTimeout | None = None
        if thread.is_alive() and self.installation_exception is None:
            self.fail_open.set()
            remaining = max(0.0, deadline - time.monotonic())
            try:
                self.submit(CommandKind.STOP, timeout=remaining)
            except (HookStopped, HookTimeout) as exc:
                stop_error = exc

        remaining = max(0.0, deadline - time.monotonic())
        thread.join(remaining)
        if thread.is_alive():
            if stop_error is not None:
                raise stop_error
            raise HookTimeout("Timed out stopping keyboard hook.")
        self._raise_cleanup_error_if_needed()

    def submit(
        self,
        kind: CommandKind,
        payload: object = None,
        *,
        timeout: float = 1.0,
    ) -> CommandResult:
        timeout = _require_finite_timeout(timeout, "submit")
        if not isinstance(kind, CommandKind):
            raise ValueError("Unknown keyboard hook command.")
        if self.fail_open.is_set() and kind not in (
            CommandKind.FAIL_OPEN,
            CommandKind.STOP,
        ):
            raise HookStopped("Keyboard hook is in fail-open mode.")
        if not self.is_alive() or self.thread_id is None:
            raise HookStopped("Keyboard hook thread is not running.")

        if kind in (CommandKind.FAIL_OPEN, CommandKind.STOP):
            self.fail_open.set()

        reply: queue.SimpleQueue[CommandResult] = queue.SimpleQueue()
        command = self._create_command(kind, payload, reply)
        try:
            self._post_command(command)
        except BaseException:
            self._remove_command(command)
            raise

        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self._cancel_command(command)
                raise HookTimeout("Timed out waiting for keyboard hook command.")
            try:
                result = reply.get(timeout=remaining)
            except queue.Empty as exc:
                self._cancel_command(command)
                raise HookTimeout("Timed out waiting for keyboard hook command.") from exc
            if getattr(result, "command_id", None) != command.command_id:
                continue
            return result

    def enter_fail_open(self) -> None:
        self.fail_open.set()
        if not self.is_alive() or self.thread_id is None:
            return
        command = self._create_command(CommandKind.FAIL_OPEN)
        try:
            self._post_command(command)
        except BaseException:
            self._remove_command(command)

    def _create_command(
        self,
        kind: CommandKind,
        payload: object = None,
        reply: queue.SimpleQueue | None = None,
    ) -> HookCommand:
        with self._pending_lock:
            command = HookCommand(
                self._next_command_id,
                kind,
                payload,
                reply,
            )
            self._next_command_id += 1
            self._pending_commands[command.command_id] = command
            return command

    def _remove_command(self, command: HookCommand) -> None:
        with self._pending_lock:
            if self._pending_commands.get(command.command_id) is command:
                del self._pending_commands[command.command_id]

    def _cancel_command(self, command: HookCommand) -> None:
        command.lifecycle.cancel()
        with self._pending_lock:
            if self._pending_commands.get(command.command_id) is command:
                del self._pending_commands[command.command_id]

    def _post_command(self, command: HookCommand) -> None:
        thread_id = self.thread_id
        if thread_id is None or not self.is_alive():
            self._remove_command(command)
            raise HookStopped("Keyboard hook thread is not running.")
        try:
            posted = self.api.post_thread_message(
                thread_id,
                WM_APP_COMMAND,
                command.command_id,
                0,
            )
        except BaseException as exc:
            self._remove_command(command)
            raise HookStopped("Keyboard hook thread stopped accepting messages.") from exc
        if not posted:
            self._remove_command(command)
            raise HookStopped("Keyboard hook thread stopped accepting messages.")

    def _run(self) -> None:
        try:
            get_thread_id = getattr(self.api, "get_current_thread_id", None)
            self.thread_id = int(
                get_thread_id()
                if get_thread_id is not None
                else getattr(self.api, "thread_id")
            )
            self.api.initialize_message_queue()
            if self._startup_cancelled.is_set():
                return
            self.callback = HOOKPROC(self._callback)
            if self._startup_cancelled.is_set():
                return
            handle = self.api.install_hook(self.callback)
            with self._startup_condition:
                with self._hook_lock:
                    self.hook_handle = handle
                if self._startup_cancelled.is_set():
                    self.fail_open.set()
                    cancel_install = True
                else:
                    self.ready.set()
                    self._startup_condition.notify_all()
                    cancel_install = False
            if cancel_install:
                return
            self._message_loop()
        except BaseException as exc:
            if not self.ready.is_set():
                with self._startup_condition:
                    self.installation_exception = exc
                    self.fail_open.set()
                    self.ready.set()
                    self._startup_condition.notify_all()
            else:
                self._fail_open("message_loop", exc)
        finally:
            with self._startup_condition:
                self.ready.set()
                self._startup_condition.notify_all()
            self._stop_requested.set()
            self.fail_open.set()
            self._force_unlocked()
            self._unhook_owner()
            self._reject_pending_commands()

    def _message_loop(self) -> None:
        message = wintypes.MSG()
        while not self._stop_requested.is_set():
            result = self.api.get_message(message)
            if result > 0:
                if int(message.message) == WM_APP_COMMAND:
                    self._dispatch_command_message(message)
                else:
                    self.api.translate(message)
                    self.api.dispatch(message)
            elif result == 0:
                return
            else:
                raise ctypes.WinError()

    def _dispatch_command_message(self, message: wintypes.MSG) -> None:
        command_id = int(message.wParam)
        with self._pending_lock:
            command = self._pending_commands.pop(command_id, None)
        if command is not None:
            self._dispatch_command(command)

    def _dispatch_command(self, command: HookCommand) -> None:
        accepted = False
        stop = command.kind is CommandKind.STOP
        try:
            active = command.lifecycle.begin(cancellable=not stop)
            if active:
                accepted = self._run_command(command)
        except BaseException as exc:
            self._fail_open("command", exc)
            accepted = False

        result = CommandResult(
            command.command_id,
            accepted,
            False if self.fail_open.is_set() else self.state.locked,
            self.shortcut_generation,
        )
        if command.reply is not None:
            command.reply.put(result)

    def _run_command(self, command: HookCommand) -> bool:
        if command.kind is CommandKind.STOP:
            self.fail_open.set()
            self._force_unlocked()
            self._stop_requested.set()
            accepted = self._unhook_owner()
            self._post_quit_owner()
            return accepted
        if command.kind is CommandKind.FAIL_OPEN:
            self.fail_open.set()
            committed, transition = command.lifecycle.commit(
                lambda: self.state.set_locked(False)
            )
            if not committed:
                return False
            if transition.changed:
                self.events.put(EngineEvent("state", False, "fail_open"))
            return True
        if self.fail_open.is_set():
            return False
        if command.kind is CommandKind.SET_LOCKED:
            committed, transition = command.lifecycle.commit(
                lambda: self.state.set_locked(bool(command.payload))
            )
            if not committed:
                return False
            self._publish_transition(transition)
            return True
        if command.kind is CommandKind.TOGGLE:
            committed, transition = command.lifecycle.commit(
                lambda: self.state.set_locked(not self.state.locked)
            )
            if not committed:
                return False
            self._publish_transition(transition)
            return True
        if command.kind is CommandKind.REPLACE_SHORTCUT:
            def replace_shortcut():
                accepted = self.state.replace_shortcut(command.payload)
                if accepted:
                    self.shortcut_generation += 1
                return accepted

            committed, accepted = command.lifecycle.commit(replace_shortcut)
            if not committed:
                return False
            return accepted
        if command.kind is CommandKind.ENTER_RECORDING:
            committed, accepted = command.lifecycle.commit(
                self.state.enter_recording
            )
            return committed and accepted
        if command.kind is CommandKind.EXIT_RECORDING:
            committed, _ = command.lifecycle.commit(self.state.exit_recording)
            return committed
        return False

    def _publish_transition(self, transition) -> None:
        if transition.changed:
            self.events.put(
                EngineEvent("state", transition.locked, transition.reason)
            )

    def _force_unlocked(self) -> None:
        try:
            self.state.set_locked(False)
        except BaseException:
            pass

    def _post_quit_owner(self) -> None:
        try:
            self.api.post_quit(0)
        except BaseException:
            pass

    def _reject_pending_commands(self) -> None:
        with self._pending_lock:
            commands = tuple(self._pending_commands.values())
            self._pending_commands.clear()
        for command in commands:
            if command.reply is not None:
                command.reply.put(
                    CommandResult(
                        command.command_id,
                        False,
                        False,
                        self.shortcut_generation,
                    )
                )

    def _callback(self, n_code, w_param, l_param):
        if n_code < HC_ACTION or self.fail_open.is_set():
            return self.api.call_next(self.hook_handle, n_code, w_param, l_param)
        try:
            data = ctypes.cast(l_param, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
            event = event_from_message(int(w_param), data)
            if event is None:
                return self.api.call_next(self.hook_handle, n_code, w_param, l_param)
            transition = self.state.handle(event)
            if transition.changed:
                self.events.put(EngineEvent("state", transition.locked, transition.reason))
            if transition.suppress:
                return 1
            return self.api.call_next(self.hook_handle, n_code, w_param, l_param)
        except BaseException as exc:
            self.fail_open.set()
            self.state.set_locked(False)
            self.events.put(EngineEvent("fatal", False, "callback", exc))
            return self.api.call_next(self.hook_handle, n_code, w_param, l_param)

    def _fail_open(self, reason: str, error: BaseException) -> None:
        self.fail_open.set()
        try:
            transition = self.state.set_locked(False)
            if transition.changed:
                self.events.put(EngineEvent("state", False, transition.reason))
        except BaseException:
            pass
        self.events.put(EngineEvent("fatal", False, reason, error))

    def _raise_cleanup_error_if_needed(self) -> None:
        error = self.cleanup_exception
        if error is not None or self.hook_handle is not None:
            if error is None:
                error = HookStopped("Keyboard hook cleanup did not complete.")
            raise HookStopped("Failed to unhook keyboard hook.") from error

    def _report_cleanup_error(self, error: BaseException) -> None:
        self.cleanup_exception = error
        self.fail_open.set()
        self.events.put(EngineEvent("fatal", False, "cleanup", error))

    def _unhook_owner(self) -> bool:
        with self._hook_lock:
            handle = self.hook_handle
        if handle is None:
            return True
        try:
            unhooked = self.api.unhook(handle)
        except BaseException as exc:
            self._report_cleanup_error(exc)
            return False
        if not unhooked:
            self._report_cleanup_error(
                HookStopped("Win32 unhook did not report success.")
            )
            return False
        with self._hook_lock:
            if self.hook_handle == handle:
                self.hook_handle = None
        self.cleanup_exception = None
        return True
